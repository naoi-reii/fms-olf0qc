from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from functools import wraps
import datetime
import json
import csv
from .models import User, Facility, Booking, Notification, Announcement, NotificationTemplate, ActivityLog, IssueReport, Message

EQUIPMENT_CHOICES = [
    'HDMI Cable','Projector Remote','Microphone','Extension Cord',
    'Whiteboard Marker','Laptop','Projector Screen','Clicker/Pointer',
    'Audio Speaker','LAN Cable',
]


# ── Helpers ───────────────────────────────────────────────────

def log_activity(user, action, description='', request=None):
    ip = request.META.get('REMOTE_ADDR') if request else None
    ActivityLog.objects.create(user=user, action=action, description=description, ip_address=ip)


def send_notification(recipient, notif_type, title, message_text, booking=None, sent_by=None):
    try:
        tmpl = NotificationTemplate.objects.get(notif_type=notif_type, is_active=True)
        ctx = {'user': recipient.get_full_name() or recipient.username, 'facility': booking.facility.name if booking else '', 'date': str(booking.date) if booking else '', 'start_time': booking.start_time.strftime('%I:%M %p') if booking else '', 'end_time': booking.end_time.strftime('%I:%M %p') if booking else '', 'status': booking.get_status_display() if booking else ''}
        message_text = tmpl.render(ctx)
        title = tmpl.subject.format(**ctx) if '{' in tmpl.subject else tmpl.subject
    except (NotificationTemplate.DoesNotExist, Exception):
        pass
    return Notification.objects.create(recipient=recipient, notif_type=notif_type, title=title, message=message_text, booking=booking, sent_by=sent_by)


# ── Decorators ────────────────────────────────────────────────

def facility_management_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated: return redirect('login')
        if not request.user.can_manage_facilities():
            messages.error(request, 'You do not have permission to manage facilities.')
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper


def booking_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated: return redirect('login')
        if not request.user.can_book():
            messages.error(request, 'You do not have permission to make bookings.')
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper


def superuser_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated: return redirect('login')
        if not request.user.can_manage_users():
            messages.error(request, 'Only Superusers can access this.')
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper


def reports_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated: return redirect('login')
        if not request.user.can_view_reports():
            messages.error(request, 'Only Facility Managers can view reports.')
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper


def announcements_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated: return redirect('login')
        if not request.user.can_send_announcements():
            messages.error(request, 'Only Facility Managers can send announcements.')
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper


# ── Auth ──────────────────────────────────────────────────────

def login_view(request):
    if request.user.is_authenticated: return redirect('dashboard')
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        try:
            user_obj = User.objects.get(username=username)
            if user_obj.is_locked:
                messages.error(request, 'Account is locked. Contact administrator.')
                return render(request, 'login.html')
        except User.DoesNotExist:
            pass
        user = authenticate(request, username=username, password=password)
        if user:
            user.failed_login_attempts = 0
            user.last_active = timezone.now()
            user.save(update_fields=['failed_login_attempts', 'last_active'])
            login(request, user)
            log_activity(user, 'login', 'User logged in', request)
            return redirect('dashboard')
        try:
            u = User.objects.get(username=username)
            u.failed_login_attempts += 1
            if u.failed_login_attempts >= 5:
                u.is_locked = True
                messages.error(request, 'Account locked after 5 failed attempts.')
            else:
                messages.error(request, f'Invalid credentials. {5 - u.failed_login_attempts} attempt(s) remaining.')
            u.save(update_fields=['failed_login_attempts', 'is_locked'])
        except User.DoesNotExist:
            messages.error(request, 'Invalid username or password.')
    return render(request, 'login.html')


def logout_view(request):
    if request.user.is_authenticated:
        log_activity(request.user, 'logout', 'User logged out', request)
    logout(request)
    return redirect('login')


# ── Dashboard ─────────────────────────────────────────────────

@login_required(login_url='login')
def dashboard_view(request):
    request.user.last_active = timezone.now()
    request.user.save(update_fields=['last_active'])
    today = datetime.date.today()
    if request.user.can_manage_facilities():
        yesterday = timezone.now() - datetime.timedelta(hours=24)
        for b in Booking.objects.filter(status='pending', created_at__lte=yesterday):
            if not Notification.objects.filter(booking=b, notif_type='escalation', recipient=request.user).exists():
                Notification.objects.create(recipient=request.user, notif_type='escalation', title=f'Pending 24h: {b.facility.name}', message=f'Booking #{b.pk} by {b.booked_by.get_full_name() or b.booked_by.username} has been pending for over 24 hours.', booking=b)
    context = {
        'total_facilities': Facility.objects.count(),
        'active_facilities': Facility.objects.filter(status='active').count(),
        'bookings_today': Booking.objects.filter(date=today).count(),
        'recent_bookings': Booking.objects.select_related('facility', 'booked_by').all()[:5],
    }
    return render(request, 'dashboard.html', context)


# ── Module 1: Facility Management ─────────────────────────────

@login_required(login_url='login')
def facilities_view(request):
    floor_filter = request.GET.get('floor', '')
    status_filter = request.GET.get('status', '')
    type_filter = request.GET.get('type', '')
    facs = Facility.objects.all()
    if floor_filter: facs = facs.filter(floor=floor_filter)
    if status_filter: facs = facs.filter(status=status_filter)
    if type_filter: facs = facs.filter(facility_type=type_filter)
    facs = facs.order_by('floor', 'name')
    grouped = {}
    for f in facs:
        grouped.setdefault(f.floor or 'Other', []).append(f)
    return render(request, 'facilities.html', {'grouped_facilities': grouped, 'floor_filter': floor_filter, 'status_filter': status_filter, 'type_filter': type_filter, 'floor_choices': Facility.FLOOR_CHOICES, 'total': facs.count()})


@login_required(login_url='login')
def facility_detail_view(request, pk):
    facility = get_object_or_404(Facility, pk=pk)
    return render(request, 'facility_detail.html', {'facility': facility, 'recent_bookings': facility.bookings.select_related('booked_by').order_by('-created_at')[:5]})


@login_required(login_url='login')
@facility_management_required
def facility_create_view(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        location = request.POST.get('location', '').strip()
        if name and location:
            f = Facility(name=name, location=location, building=request.POST.get('building', 'CAS Building').strip(), floor=request.POST.get('floor', ''), facility_type=request.POST.get('facility_type', 'classroom'), capacity=request.POST.get('capacity', 0), description=request.POST.get('description', '').strip(), tags=request.POST.get('tags', '').strip(), custodian=request.POST.get('custodian', '').strip(), availability_start=request.POST.get('availability_start') or None, availability_end=request.POST.get('availability_end') or None, is_restricted=request.POST.get('is_restricted') == 'on', allowed_roles=request.POST.get('allowed_roles', '').strip(), created_by=request.user)
            if 'image' in request.FILES: f.image = request.FILES['image']
            if 'floor_plan' in request.FILES: f.floor_plan = request.FILES['floor_plan']
            f.save()
            log_activity(request.user, 'create_facility', f'Created: {name}', request)
            messages.success(request, f'Facility "{name}" created.')
            return redirect('facilities')
        messages.error(request, 'Name and location are required.')
    return render(request, 'facility_form.html', {'action': 'Create', 'floor_choices': Facility.FLOOR_CHOICES, 'type_choices': Facility.TYPE_CHOICES, 'status_choices': Facility.STATUS_CHOICES})


@login_required(login_url='login')
@facility_management_required
def facility_edit_view(request, pk):
    facility = get_object_or_404(Facility, pk=pk)
    if request.method == 'POST':
        for field in ['name', 'location', 'building', 'floor', 'facility_type', 'capacity', 'description', 'tags', 'custodian', 'status']:
            val = request.POST.get(field, getattr(facility, field))
            setattr(facility, field, str(val).strip() if isinstance(val, str) else val)
        facility.availability_start = request.POST.get('availability_start') or None
        facility.availability_end = request.POST.get('availability_end') or None
        facility.is_restricted = request.POST.get('is_restricted') == 'on'
        facility.allowed_roles = request.POST.get('allowed_roles', '').strip()
        if 'image' in request.FILES: facility.image = request.FILES['image']
        if 'floor_plan' in request.FILES: facility.floor_plan = request.FILES['floor_plan']
        if request.POST.get('clear_image'): facility.image = None
        if request.POST.get('clear_floor_plan'): facility.floor_plan = None
        facility.save()
        log_activity(request.user, 'edit_facility', f'Edited: {facility.name}', request)
        messages.success(request, f'Facility "{facility.name}" updated.')
        return redirect('facility_detail', pk=facility.pk)
    return render(request, 'facility_form.html', {'facility': facility, 'action': 'Edit', 'floor_choices': Facility.FLOOR_CHOICES, 'type_choices': Facility.TYPE_CHOICES, 'status_choices': Facility.STATUS_CHOICES})


@login_required(login_url='login')
@facility_management_required
def facility_set_maintenance_view(request, pk):
    """Quick toggle for IT staff from issue detail page."""
    facility = get_object_or_404(Facility, pk=pk)
    if request.method == 'POST':
        new_status = request.POST.get('status', 'maintenance')
        facility.status = new_status
        facility.save()
        messages.success(request, f'"{facility.name}" is now {facility.get_status_display()}.')
    next_url = request.POST.get('next', request.GET.get('next', 'facilities'))
    return redirect(next_url)


# ── Room Schedule ──────────────────────────────────────────────

@login_required(login_url='login')
def room_schedule_view(request):
    selected_date_str = request.GET.get('date', '')
    selected_facility = request.GET.get('facility', '')
    today = datetime.date.today()
    try:
        selected_date = datetime.date.fromisoformat(selected_date_str) if selected_date_str else today
    except ValueError:
        selected_date = today

    facs = Facility.objects.all().order_by('floor', 'name')
    if selected_facility:
        facs = facs.filter(pk=selected_facility)

    schedule = []
    for facility in facs:
        bookings = list(Booking.objects.filter(facility=facility, date=selected_date, status__in=[Booking.APPROVED, Booking.PENDING]).select_related('booked_by').order_by('start_time'))
        for b in bookings:
            if b.equipment_needed and '|' in b.equipment_needed:
                parts = b.equipment_needed.split('|', 1)
                b.equipment_items_list = [x.strip() for x in parts[0].split(',') if x.strip()]
                b.equipment_notes_text = parts[1].strip()
            elif b.equipment_needed:
                chips = [x.strip() for x in b.equipment_needed.split(',') if x.strip() in EQUIPMENT_CHOICES]
                b.equipment_items_list = chips
                b.equipment_notes_text = b.equipment_needed if not chips else ''
            else:
                b.equipment_items_list = []
                b.equipment_notes_text = ''
        schedule.append({'facility': facility, 'bookings': bookings})

    stats = {
        'total_bookings': Booking.objects.filter(date=selected_date).count(),
        'approved': Booking.objects.filter(date=selected_date, status='approved').count(),
        'pending': Booking.objects.filter(date=selected_date, status='pending').count(),
        'equipment_requests': Booking.objects.filter(date=selected_date).exclude(equipment_needed='').count(),
    }
    return render(request, 'room_schedule.html', {'schedule': schedule, 'all_facilities': Facility.objects.all().order_by('floor', 'name'), 'selected_date': str(selected_date), 'selected_facility': selected_facility, 'stats': stats})


# ── Module 2: Booking ─────────────────────────────────────────

def check_booking_conflict(facility, date, start_time, end_time, exclude_pk=None):
    conflicts = Booking.objects.filter(facility=facility, date=date, status__in=[Booking.PENDING, Booking.APPROVED]).exclude(pk=exclude_pk or 0)
    return [b for b in conflicts if b.start_time < end_time and b.end_time > start_time]


@login_required(login_url='login')
def bookings_view(request):
    view_mode = request.GET.get('view', 'list')
    status_filter = request.GET.get('status', '')
    facility_filter = request.GET.get('facility', '')
    bookings = Booking.objects.select_related('facility', 'booked_by').all() if request.user.can_manage_facilities() else Booking.objects.filter(booked_by=request.user).select_related('facility')
    if status_filter: bookings = bookings.filter(status=status_filter)
    if facility_filter: bookings = bookings.filter(facility_id=facility_filter)
    today = datetime.date.today()
    cal = [{'id': b.pk, 'title': f'{b.facility.name} — {b.booked_by.get_full_name() or b.booked_by.username}', 'start': f'{b.date}T{b.start_time}', 'end': f'{b.date}T{b.end_time}', 'color': '#1a6b3a' if b.status == 'approved' else '#b7770d', 'status': b.status, 'facility': b.facility.name, 'booked_by': b.booked_by.get_full_name() or b.booked_by.username, 'purpose': b.purpose} for b in Booking.objects.select_related('facility', 'booked_by').filter(status__in=['pending', 'approved'])]
    stats = {'total': Booking.objects.count() if request.user.can_manage_facilities() else Booking.objects.filter(booked_by=request.user).count(), 'pending': Booking.objects.filter(status='pending').count() if request.user.can_manage_facilities() else Booking.objects.filter(booked_by=request.user, status='pending').count(), 'approved': Booking.objects.filter(status='approved').count() if request.user.can_manage_facilities() else Booking.objects.filter(booked_by=request.user, status='approved').count(), 'today': Booking.objects.filter(date=today).count()}
    return render(request, 'bookings.html', {'bookings': bookings, 'calendar_bookings_json': json.dumps(cal), 'view_mode': view_mode, 'status_filter': status_filter, 'facility_filter': facility_filter, 'facilities': Facility.objects.filter(status='active'), 'stats': stats, 'today': today})


@login_required(login_url='login')
def booking_check_conflict(request):
    if request.method != 'POST': return JsonResponse({'error': 'POST required'}, status=405)
    try:
        data = json.loads(request.body)
        facility = Facility.objects.get(pk=data.get('facility_id'))
        date = datetime.date.fromisoformat(data.get('date'))
        start_time = datetime.time.fromisoformat(data.get('start_time'))
        end_time = datetime.time.fromisoformat(end_str := data.get('end_time'))
        
        now = timezone.localtime(timezone.now())
        if start_time >= end_time: return JsonResponse({'conflict': False, 'error': 'End time must be after start time.'})
        if date < now.date(): return JsonResponse({'conflict': False, 'error': 'Cannot book a past date.'})
        if date == now.date() and start_time < now.time(): return JsonResponse({'conflict': False, 'error': 'Cannot book a past time today.'})
        
        conflicts = check_booking_conflict(facility, date, start_time, end_time, data.get('exclude_pk'))
        if conflicts:
            suggestions = []
            for offset in [1, 2, 3]:
                alt_start = (datetime.datetime.combine(date, end_time) + datetime.timedelta(hours=offset)).time()
                duration = datetime.datetime.combine(date, end_time) - datetime.datetime.combine(date, start_time)
                alt_end = (datetime.datetime.combine(date, alt_start) + duration).time()
                if not check_booking_conflict(facility, date, alt_start, alt_end, data.get('exclude_pk')) and alt_end.hour < 21:
                    suggestions.append({'start': str(alt_start)[:5], 'end': str(alt_end)[:5]})
                    if len(suggestions) >= 2: break
            return JsonResponse({'conflict': True, 'conflicts': [{'booked_by': c.booked_by.get_full_name() or c.booked_by.username, 'start_time': str(c.start_time), 'end_time': str(c.end_time), 'status': c.get_status_display()} for c in conflicts], 'suggestions': suggestions})
        return JsonResponse({'conflict': False})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required(login_url='login')
@booking_required
def booking_create_view(request):
    facilities = Facility.objects.filter(status='active')
    preselect_facility = request.GET.get('facility', '')
    if request.method == 'POST':
        facility_id = request.POST.get('facility')
        date_str = request.POST.get('date')
        start_str = request.POST.get('start_time')
        end_str = request.POST.get('end_time')
        purpose = request.POST.get('purpose', '').strip()
        attendees = request.POST.get('number_of_attendees', 1)
        force = request.POST.get('force_submit') == '1'
        equipment_items = request.POST.getlist('equipment_items')
        equipment_needed = request.POST.get('equipment_needed', '').strip()
        all_eq = (', '.join(equipment_items) + (' | ' + equipment_needed if equipment_needed else '')) if equipment_items else equipment_needed

        if facility_id and date_str and start_str and end_str:
            facility = get_object_or_404(Facility, pk=facility_id)
            date = datetime.date.fromisoformat(date_str)
            start_time = datetime.time.fromisoformat(start_str)
            end_time = datetime.time.fromisoformat(end_str)
            
            now = timezone.localtime(timezone.now())
            if start_time >= end_time: messages.error(request, 'End time must be after start time.')
            elif date < now.date(): messages.error(request, 'Cannot book a past date.')
            elif date == now.date() and start_time < now.time(): messages.error(request, 'Cannot book a past time today.')
            else:
                conflicts = check_booking_conflict(facility, date, start_time, end_time)
                if conflicts and not force:
                    return render(request, 'booking_form.html', {'facilities': facilities, 'has_conflict': True, 'conflict_info': conflicts, 'form_data': request.POST, 'preselect': facility_id, 'equipment_choices': EQUIPMENT_CHOICES, 'today': datetime.date.today().isoformat()})
                booking = Booking.objects.create(facility=facility, booked_by=request.user, date=date, start_time=start_time, end_time=end_time, purpose=purpose, number_of_attendees=attendees, equipment_needed=all_eq)
                send_notification(request.user, 'confirmation', f'Booking Submitted: {facility.name}', f'Your booking for {facility.name} on {date} is pending approval.', booking=booking)
                if all_eq:
                    it_staff = User.objects.filter(role__in=[User.TECHNICAL_STAFF, User.FACILITY_MANAGER, User.SUPERUSER_ROLE])
                    for staff in it_staff:
                        Notification.objects.create(recipient=staff, notif_type='confirmation', title=f'Equipment Request: {facility.name}', message=f'{request.user.get_full_name() or request.user.username} needs: {all_eq}\nFor: {facility.name} on {date} at {start_time.strftime("%I:%M %p")}', booking=booking, sent_by=request.user)
                log_activity(request.user, 'book', f'Booked {facility.name} on {date}', request)
                messages.success(request, f'Booking #{booking.pk} submitted!' + (f' Equipment request sent to IT Department.' if all_eq else ''))
                return redirect('bookings')
        else: messages.error(request, 'All fields are required.')
    return render(request, 'booking_form.html', {'facilities': facilities, 'preselect': preselect_facility, 'today': datetime.date.today().isoformat(), 'equipment_choices': EQUIPMENT_CHOICES})


@login_required(login_url='login')
def booking_detail_view(request, pk):
    booking = get_object_or_404(Booking, pk=pk) if request.user.can_manage_facilities() else get_object_or_404(Booking, pk=pk, booked_by=request.user)
    eq_items, eq_notes = [], ''
    if booking.equipment_needed:
        if '|' in booking.equipment_needed:
            parts = booking.equipment_needed.split('|', 1)
            eq_items = [x.strip() for x in parts[0].split(',') if x.strip()]
            eq_notes = parts[1].strip()
        else:
            eq_items = [x.strip() for x in booking.equipment_needed.split(',') if x.strip() in EQUIPMENT_CHOICES]
            eq_notes = booking.equipment_needed if not eq_items else ''

    available_alternatives = []
    if booking.status in [Booking.PENDING, Booking.APPROVED]:
        all_active_facilities = Facility.objects.filter(status='active').exclude(pk=booking.facility.pk)
        for alt_fac in all_active_facilities:
            conflicts = check_booking_conflict(alt_fac, booking.date, booking.start_time, booking.end_time)
            if not conflicts:
                available_alternatives.append(alt_fac)

    return render(request, 'booking_detail.html', {'booking': booking, 'eq_items': eq_items, 'eq_notes': eq_notes, 'available_alternatives': available_alternatives})


@login_required(login_url='login')
@facility_management_required
def booking_approve_view(request, pk):
    booking = get_object_or_404(Booking, pk=pk)
    action = request.POST.get('action')
    if action in ['approve', 'reject']:
        if not request.user.can_approve_bookings():
            messages.error(request, 'Only Facility Managers can approve or reject bookings.')
            return redirect('booking_detail', pk=pk)
        
        if action == 'approve':
            booking.status = Booking.APPROVED; booking.approved_by = request.user; booking.save()
            send_notification(booking.booked_by, 'approval', f'Booking Approved: {booking.facility.name}', '', booking=booking, sent_by=request.user)
            log_activity(request.user, 'approve', f'Approved #{pk}', request)
            messages.success(request, f'Booking #{pk} approved.')
        elif action == 'reject':
            booking.status = Booking.REJECTED; booking.save()
            send_notification(booking.booked_by, 'rejection', f'Booking Rejected: {booking.facility.name}', '', booking=booking, sent_by=request.user)
            log_activity(request.user, 'reject', f'Rejected #{pk}', request)
            messages.warning(request, f'Booking #{pk} rejected.')
    
    elif action == 'equipment_prepared':
        booking.equipment_status = 'prepared'; booking.save()
        messages.success(request, 'Equipment marked as prepared.')
    elif action == 'equipment_delivered':
        booking.equipment_status = 'delivered'; booking.save()
        send_notification(booking.booked_by, 'confirmation', f'Equipment Ready: {booking.facility.name}', f'Your requested equipment for {booking.facility.name} on {booking.date} is now ready.', booking=booking, sent_by=request.user)
        messages.success(request, 'Equipment marked as delivered. Prof notified.')
    return redirect('bookings')


@login_required(login_url='login')
def booking_cancel_view(request, pk):
    booking = get_object_or_404(Booking, pk=pk, booked_by=request.user)
    if booking.status == Booking.PENDING:
        booking.status = Booking.CANCELLED; booking.save()
        send_notification(request.user, 'cancellation', f'Booking Cancelled: {booking.facility.name}', '', booking=booking)
        log_activity(request.user, 'cancel', f'Cancelled #{pk}', request)
        messages.success(request, 'Booking cancelled.')
    else: messages.error(request, 'Only pending bookings can be cancelled.')
    return redirect('bookings')


@login_required(login_url='login')
def booking_request_change_view(request, pk):
    if request.method != 'POST':
        return redirect('booking_detail', pk=pk)

    booking = get_object_or_404(Booking, pk=pk, booked_by=request.user)
    if booking.status not in [Booking.PENDING, Booking.APPROVED]:
        messages.error(request, 'Room changes can only be requested for pending or approved bookings.')
        return redirect('booking_detail', pk=pk)

    reason = request.POST.get('reason', '').strip()
    preferred_alt_id = request.POST.get('preferred_alternative')

    if not reason:
        messages.error(request, 'A reason is required to request a room change.')
        return redirect('booking_detail', pk=pk)

    preferred_fac = None
    if preferred_alt_id:
        preferred_fac = Facility.objects.filter(pk=preferred_alt_id, status='active').first()

    issue = IssueReport.objects.create(
        facility=booking.facility,
        reported_by=request.user,
        booking=booking,
        category='other',
        title=f'Room Change Request for Booking #{booking.pk}',
        description=reason,
        priority='medium',
        requesting_room_change=True,
        status='room_change_requested',
        preferred_alternative=preferred_fac
    )

    it_staff = User.objects.filter(role__in=[User.TECHNICAL_STAFF, User.FACILITY_MANAGER, User.SUPERUSER_ROLE])
    notif_title = f'[ROOM CHANGE] Request for Booking #{booking.pk}'
    notif_msg = f"{request.user.get_full_name() or request.user.username} requested a room change for their booking at {booking.facility.name}.\nReason: {reason}"
    if preferred_fac:
        notif_msg += f"\nPreferred Alternative: {preferred_fac.name}"

    for staff in it_staff:
        Notification.objects.create(
            recipient=staff,
            notif_type='announcement',
            title=notif_title,
            message=notif_msg,
            sent_by=request.user,
            booking=booking
        )

    messages.success(request, 'Room change request submitted successfully.')
    return redirect('booking_detail', pk=pk)


# ── Issue Reports ──────────────────────────────────────────────

@login_required(login_url='login')
def issue_list_view(request):
    status_filter = request.GET.get('status', '')
    priority_filter = request.GET.get('priority', '')
    if request.user.is_it_staff():
        issues = IssueReport.objects.select_related('facility', 'reported_by').all()
    else:
        issues = IssueReport.objects.filter(reported_by=request.user).select_related('facility')
    if status_filter: issues = issues.filter(status=status_filter)
    if priority_filter: issues = issues.filter(priority=priority_filter)
    return render(request, 'issue_list.html', {'issues': issues, 'status_filter': status_filter, 'priority_filter': priority_filter, 'status_choices': IssueReport.STATUS_CHOICES, 'priority_choices': IssueReport.PRIORITY_CHOICES})


@login_required(login_url='login')
def issue_report_create_view(request):
    facilities = Facility.objects.all().order_by('floor', 'name')
    available = Facility.objects.filter(status='active')
    my_bookings = Booking.objects.filter(booked_by=request.user, status__in=['approved', 'pending']).order_by('-date')[:10]
    preselect_facility = request.GET.get('facility', '')
    preselect_booking = request.GET.get('booking', '')
    request_room_change = request.GET.get('request_room_change') == 'true'

    booking = None
    if preselect_booking:
        try:
            booking = Booking.objects.get(pk=preselect_booking)
            if not preselect_facility:
                preselect_facility = str(booking.facility.pk)
            
            # Filter available rooms for this booking's time slot
            conflicting_facility_ids = Booking.objects.filter(
                date=booking.date,
                status__in=[Booking.PENDING, Booking.APPROVED]
            ).filter(
                start_time__lt=booking.end_time,
                end_time__gt=booking.start_time
            ).exclude(pk=booking.pk).values_list('facility_id', flat=True)
            
            available = Facility.objects.filter(status='active').exclude(pk__in=conflicting_facility_ids)
        except Booking.DoesNotExist:
            pass

    if request.method == 'POST':
        fac_id = request.POST.get('facility')
        if not fac_id:
            messages.error(request, 'Please select a facility.')
            return render(request, 'issue_report_form.html', {
                'facilities': facilities, 'available_facilities': available, 
                'my_bookings': my_bookings, 'category_choices': IssueReport.CATEGORY_CHOICES, 
                'priority_choices': IssueReport.PRIORITY_CHOICES, 'preselect_facility': preselect_facility, 
                'preselect_booking': preselect_booking, 'request_room_change': request_room_change,
                'booking': booking
            })

        facility = get_object_or_404(Facility, pk=fac_id)
        issue = IssueReport(
            facility=facility, reported_by=request.user,
            category=request.POST.get('category', 'other'),
            title=request.POST.get('title', '').strip(),
            description=request.POST.get('description', '').strip(),
            priority=request.POST.get('priority', 'medium'),
            requesting_room_change=request.POST.get('requesting_room_change') == 'on',
        )
        booking_id = request.POST.get('booking')
        if booking_id:
            try: issue.booking = Booking.objects.get(pk=booking_id)
            except Booking.DoesNotExist: pass
        preferred_id = request.POST.get('preferred_alternative')
        if preferred_id:
            try: issue.preferred_alternative = Facility.objects.get(pk=preferred_id)
            except Facility.DoesNotExist: pass
        
        if issue.requesting_room_change:
            issue.status = 'room_change_requested'
        issue.save()

        # Notify IT staff
        it_staff = User.objects.filter(role__in=[User.TECHNICAL_STAFF, User.FACILITY_MANAGER, User.SUPERUSER_ROLE])
        notif_title = f'[{issue.get_priority_display().upper()}] Issue: {issue.title}'
        notif_msg = f'{request.user.get_full_name() or request.user.username} reported: {issue.description[:150]}'
        if issue.requesting_room_change:
            notif_msg += '\n⚠ Room change requested.'
        for staff in it_staff:
            Notification.objects.create(recipient=staff, notif_type='announcement', title=notif_title, message=notif_msg, sent_by=request.user)

        messages.success(request, f'Issue reported successfully. IT Department has been notified.')
        return redirect('issue_list')

    return render(request, 'issue_report_form.html', {
        'facilities': facilities, 'available_facilities': available, 
        'my_bookings': my_bookings, 'category_choices': IssueReport.CATEGORY_CHOICES, 
        'priority_choices': IssueReport.PRIORITY_CHOICES, 'preselect_facility': preselect_facility, 
        'preselect_booking': preselect_booking, 'request_room_change': request_room_change,
        'booking': booking
    })


@login_required(login_url='login')
def issue_detail_view(request, pk):
    issue = get_object_or_404(IssueReport, pk=pk) if request.user.is_it_staff() else get_object_or_404(IssueReport, pk=pk, reported_by=request.user)
    
    # Mark messages as read for the current user
    Message.objects.filter(issue_report=issue, recipient=request.user, is_read=False).update(is_read=True)
    
    msgs = Message.objects.filter(issue_report=issue).select_related('sender').order_by('created_at')
    available_facilities = Facility.objects.filter(status='active').exclude(pk=issue.facility.pk)
    return render(request, 'issue_detail.html', {'issue': issue, 'message_thread': msgs, 'available_facilities': available_facilities, 'status_choices': IssueReport.STATUS_CHOICES})


@login_required(login_url='login')
@facility_management_required
def issue_update_view(request, pk):
    issue = get_object_or_404(IssueReport, pk=pk)
    if request.method == 'POST':
        original_status = issue.status
        issue.status = request.POST.get('status', issue.status)
        issue.resolution_notes = request.POST.get('resolution_notes', issue.resolution_notes).strip()
        issue.assigned_to = request.user

        if issue.requesting_room_change and issue.status == 'room_change_approved' and original_status != 'room_change_approved':
            new_fac_id = request.POST.get('new_facility')
            if new_fac_id:
                new_fac = get_object_or_404(Facility, pk=new_fac_id)
                issue.room_change_new_facility = new_fac
                issue.room_change_approved_by = request.user
                # Update the related booking
                if issue.booking:
                    issue.booking.facility = new_fac
                    issue.booking.save()
                    Notification.objects.create(recipient=issue.reported_by, notif_type='approval', title=f'Room Change Approved: {new_fac.name}', message=f'Your room change request has been approved. Your booking has been moved to {new_fac.name}.', booking=issue.booking, sent_by=request.user)
                else:
                    Notification.objects.create(recipient=issue.reported_by, notif_type='approval', title=f'Room Change Approved: {new_fac.name}', message=f'Your room change request has been approved. New room: {new_fac.name}.', sent_by=request.user)
                # Set original room to maintenance
                issue.facility.status = 'maintenance'
                issue.facility.save()
                messages.success(request, f'Room changed to {new_fac.name}. Original room set to maintenance.')
            else:
                issue.status = original_status
                messages.error(request, 'Please select a new room.')
                return redirect('issue_detail', pk=pk)

        if issue.status == 'resolved' and original_status != 'resolved':
            issue.resolved_at = timezone.now()
            Notification.objects.create(recipient=issue.reported_by, notif_type='confirmation', title=f'Issue Resolved: {issue.title}', message=f'Your reported issue "{issue.title}" in {issue.facility.name} has been resolved. {issue.resolution_notes}', sent_by=request.user)

        issue.save()
        messages.success(request, 'Issue updated.')
    return redirect('issue_detail', pk=pk)


# ── Messaging ─────────────────────────────────────────────────

@login_required(login_url='login')
def send_message_view(request, issue_pk):
    issue = get_object_or_404(IssueReport, pk=issue_pk)
    if request.method == 'POST':
        body = request.POST.get('body', '').strip()
        if body:
            # Determine recipient (if reporter sends, IT staff gets it; if IT sends, reporter gets it)
            if request.user == issue.reported_by:
                # Find assigned IT staff or any IT staff
                recipient = issue.assigned_to or User.objects.filter(role=User.TECHNICAL_STAFF).first() or User.objects.filter(role=User.FACILITY_MANAGER).first()
            else:
                recipient = issue.reported_by

            if recipient:
                msg = Message.objects.create(sender=request.user, recipient=recipient, body=body, issue_report=issue)
                # In-app notification
                Notification.objects.create(recipient=recipient, notif_type='announcement', title=f'New message re: {issue.title}', message=f'{request.user.get_full_name() or request.user.username}: {body[:100]}', sent_by=request.user)
                messages.success(request, 'Message sent.')
            else:
                messages.error(request, 'Could not find recipient.')
    return redirect('issue_detail', pk=issue_pk)


# ── Module 3: User Management ─────────────────────────────────

@login_required(login_url='login')
@superuser_required
def user_list_view(request):
    from django.db.models import Q as DQ
    role_filter = request.GET.get('role', '')
    dept_filter = request.GET.get('dept', '')
    search = request.GET.get('search', '').strip()
    users = User.objects.all().order_by('role', 'last_name', 'first_name')
    if role_filter: users = users.filter(role=role_filter)
    if dept_filter: users = users.filter(department=dept_filter)
    if search: users = users.filter(DQ(username__icontains=search)|DQ(first_name__icontains=search)|DQ(last_name__icontains=search)|DQ(email__icontains=search))
    stats = {'total': User.objects.count(), 'active': User.objects.filter(is_active=True, is_locked=False).count(), 'locked': User.objects.filter(is_locked=True).count(), 'by_role': {r: User.objects.filter(role=r).count() for r, _ in User.ROLE_CHOICES}}
    return render(request, 'users.html', {'users': users, 'role_filter': role_filter, 'dept_filter': dept_filter, 'search': search, 'stats': stats, 'role_choices': User.ROLE_CHOICES, 'dept_choices': User.DEPARTMENT_CHOICES})


@login_required(login_url='login')
@superuser_required
def user_create_view(request):
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        password_confirm = request.POST.get('password_confirm', '')
        if not username or not password: messages.error(request, 'Username and password are required.')
        elif password != password_confirm: messages.error(request, 'Passwords do not match.')
        elif len(password) < 8: messages.error(request, 'Password must be at least 8 characters.')
        elif User.objects.filter(username=username).exists(): messages.error(request, 'Username already exists.')
        else:
            role = request.POST.get('role', User.STANDARD_USER)
            user = User.objects.create_user(username=username, first_name=request.POST.get('first_name','').strip(), last_name=request.POST.get('last_name','').strip(), email=request.POST.get('email','').strip(), password=password, role=role, department=request.POST.get('department',''), phone=request.POST.get('phone','').strip(), profile_notes=request.POST.get('profile_notes','').strip())
            log_activity(request.user, 'create_user', f'Created: {username} ({role})', request)
            send_notification(user, 'announcement', 'Welcome to OLFU FMS!', f'Your account has been created. Role: {user.get_role_display()}.', sent_by=request.user)
            messages.success(request, f'User "{username}" created.')
            return redirect('user_list')
    return render(request, 'user_form.html', {'action': 'Create', 'role_choices': User.ROLE_CHOICES, 'dept_choices': User.DEPARTMENT_CHOICES})


@login_required(login_url='login')
@superuser_required
def user_edit_view(request, pk):
    edit_user = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        edit_user.first_name = request.POST.get('first_name', edit_user.first_name).strip()
        edit_user.last_name = request.POST.get('last_name', edit_user.last_name).strip()
        edit_user.email = request.POST.get('email', edit_user.email).strip()
        edit_user.role = request.POST.get('role', edit_user.role)
        edit_user.department = request.POST.get('department', edit_user.department)
        edit_user.phone = request.POST.get('phone', edit_user.phone).strip()
        edit_user.profile_notes = request.POST.get('profile_notes', edit_user.profile_notes).strip()
        edit_user.is_active = request.POST.get('is_active') == 'on'
        new_pw = request.POST.get('new_password', '').strip()
        if new_pw:
            if len(new_pw) < 8: messages.error(request, 'Password must be 8+ characters.'); return render(request, 'user_form.html', {'action': 'Edit', 'edit_user': edit_user, 'role_choices': User.ROLE_CHOICES, 'dept_choices': User.DEPARTMENT_CHOICES})
            edit_user.set_password(new_pw)
        edit_user.save()
        log_activity(request.user, 'edit_user', f'Edited: {edit_user.username}', request)
        messages.success(request, f'User "{edit_user.username}" updated.')
        return redirect('user_list')
    return render(request, 'user_form.html', {'action': 'Edit', 'edit_user': edit_user, 'role_choices': User.ROLE_CHOICES, 'dept_choices': User.DEPARTMENT_CHOICES, 'activity_logs': edit_user.activity_logs.all()[:10]})


@login_required(login_url='login')
@superuser_required
def user_toggle_lock_view(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user == request.user: messages.error(request, 'Cannot lock own account.'); return redirect('user_list')
    user.is_locked = not user.is_locked; user.failed_login_attempts = 0
    user.save(update_fields=['is_locked', 'failed_login_attempts'])
    messages.success(request, f'Account "{user.username}" {"locked" if user.is_locked else "unlocked"}.')
    return redirect('user_list')


@login_required(login_url='login')
@superuser_required
def user_activity_view(request, pk):
    user = get_object_or_404(User, pk=pk)
    return render(request, 'user_activity.html', {'target_user': user, 'logs': user.activity_logs.all()[:50]})


@login_required(login_url='login')
def profile_view(request):
    if request.method == 'POST':
        request.user.first_name = request.POST.get('first_name', request.user.first_name).strip()
        request.user.last_name = request.POST.get('last_name', request.user.last_name).strip()
        request.user.email = request.POST.get('email', request.user.email).strip()
        request.user.phone = request.POST.get('phone', request.user.phone).strip()
        new_pw = request.POST.get('new_password', '')
        if new_pw:
            if not request.user.check_password(request.POST.get('old_password', '')): messages.error(request, 'Current password incorrect.'); return render(request, 'profile.html')
            if len(new_pw) < 8: messages.error(request, 'Password must be 8+ characters.'); return render(request, 'profile.html')
            request.user.set_password(new_pw)
            update_session_auth_hash(request, request.user)
        request.user.save()
        messages.success(request, 'Profile updated.')
        return redirect('profile')
    return render(request, 'profile.html', {'recent_bookings': request.user.bookings.select_related('facility').order_by('-created_at')[:5], 'recent_activity': request.user.activity_logs.all()[:5]})


# ── Module 4: Notifications ───────────────────────────────────

@login_required(login_url='login')
def notifications_view(request):
    filter_type = request.GET.get('type', '')
    notifs = request.user.notifications.all()
    if filter_type: notifs = notifs.filter(notif_type=filter_type)
    request.user.notifications.filter(is_read=False).update(is_read=True)
    return render(request, 'notifications.html', {'notifications': notifs, 'filter_type': filter_type, 'type_choices': Notification.TYPE_CHOICES, 'announcements': Announcement.objects.all()[:5]})


@login_required(login_url='login')
def notification_delete(request, pk):
    get_object_or_404(Notification, pk=pk, recipient=request.user).delete()
    messages.success(request, 'Notification deleted.')
    return redirect('notifications')


@login_required(login_url='login')
@announcements_required
def announcement_create_view(request):
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        msg_text = request.POST.get('message', '').strip()
        priority = request.POST.get('priority', 'normal')
        target_roles = request.POST.getlist('target_roles')
        if not title or not msg_text: messages.error(request, 'Title and message required.')
        else:
            ann = Announcement.objects.create(title=title, message=msg_text, priority=priority, target_roles=','.join(target_roles), sent_by=request.user)
            recipients = User.objects.filter(role__in=target_roles, is_active=True) if target_roles else User.objects.filter(is_active=True)
            count = 0
            for r in recipients:
                Notification.objects.create(recipient=r, notif_type='announcement', title=title, message=msg_text, sent_by=request.user)
                count += 1
            ann.recipient_count = count; ann.save()
            messages.success(request, f'Announcement sent to {count} users.')
            return redirect('notifications')
    return render(request, 'announcement_form.html', {'role_choices': User.ROLE_CHOICES, 'priority_choices': Announcement.PRIORITY_CHOICES})


@login_required(login_url='login')
@announcements_required
def announcement_list_view(request):
    return render(request, 'announcement_list.html', {'announcements': Announcement.objects.select_related('sent_by').all()})


@login_required(login_url='login')
@announcements_required
def template_list_view(request):
    defaults = [('confirmation','Booking Confirmed: {facility}','Hi {user}, your booking for {facility} on {date} from {start_time} to {end_time} is pending approval.'),('approval','Booking Approved: {facility}','Hi {user}, your booking for {facility} on {date} has been approved!'),('rejection','Booking Rejected: {facility}','Hi {user}, your booking for {facility} on {date} was rejected.'),('reminder','Reminder: Booking Today','Hi {user}, reminder that you have a booking for {facility} today at {start_time}.'),('cancellation','Booking Cancelled: {facility}','Hi {user}, your booking for {facility} on {date} has been cancelled.'),('escalation','Pending Approval Alert','A booking for {facility} on {date} has been pending for over 24 hours.'),('announcement','Announcement','{facility}')]
    for t, s, b in defaults:
        NotificationTemplate.objects.get_or_create(notif_type=t, defaults={'subject': s, 'body': b, 'updated_by': request.user})
    return render(request, 'notification_templates.html', {'templates': NotificationTemplate.objects.all()})


@login_required(login_url='login')
@announcements_required
def template_edit_view(request, pk):
    template = get_object_or_404(NotificationTemplate, pk=pk)
    if request.method == 'POST':
        template.subject = request.POST.get('subject', template.subject).strip()
        template.body = request.POST.get('body', template.body).strip()
        template.is_active = request.POST.get('is_active') == 'on'
        template.updated_by = request.user; template.save()
        messages.success(request, f'Template updated.')
        return redirect('notification_templates')
    return render(request, 'template_form.html', {'template': template})


# ── Module 5: Reports & Analytics ────────────────────────────

@login_required(login_url='login')
@reports_required
def reports_view(request):
    from django.db.models import Count, Q
    date_from_str = request.GET.get('date_from', '')
    date_to_str = request.GET.get('date_to', '')
    facility_type_filter = request.GET.get('facility_type', '')
    floor_filter = request.GET.get('floor', '')
    today = datetime.date.today()
    thirty_days_ago = today - datetime.timedelta(days=30)
    date_from = datetime.date.fromisoformat(date_from_str) if date_from_str else thirty_days_ago
    date_to = datetime.date.fromisoformat(date_to_str) if date_to_str else today
    bqs = Booking.objects.filter(date__gte=date_from, date__lte=date_to)
    if facility_type_filter: bqs = bqs.filter(facility__facility_type=facility_type_filter)
    if floor_filter: bqs = bqs.filter(facility__floor=floor_filter)
    total = bqs.count(); approved = bqs.filter(status='approved').count(); pending = bqs.filter(status='pending').count(); rejected = bqs.filter(status='rejected').count(); cancelled = bqs.filter(status='cancelled').count()
    approval_rate = round((approved / total * 100) if total else 0)
    facility_usage = list(Facility.objects.annotate(total=Count('bookings', filter=Q(bookings__date__gte=date_from, bookings__date__lte=date_to)), approved_count=Count('bookings', filter=Q(bookings__status='approved', bookings__date__gte=date_from, bookings__date__lte=date_to))).filter(total__gt=0).order_by('-total').values('name', 'floor', 'facility_type', 'total', 'approved_count')[:10])
    daily_labels, daily_data = [], []
    for i in range(13, -1, -1):
        d = today - datetime.timedelta(days=i); daily_labels.append(d.strftime('%b %d')); daily_data.append(Booking.objects.filter(date=d).count())
    hour_data = [Booking.objects.filter(start_time__gte=datetime.time(h,0), start_time__lte=datetime.time(h,59), status__in=['approved','pending']).count() for h in range(7, 21)]
    type_data = list(Booking.objects.filter(date__gte=date_from, date__lte=date_to).values('facility__facility_type').annotate(count=Count('id')).order_by('-count'))
    floor_data = list(Booking.objects.filter(date__gte=date_from, date__lte=date_to).values('facility__floor').annotate(count=Count('id')).order_by('facility__floor'))
    top_bookers = list(Booking.objects.filter(date__gte=date_from, date__lte=date_to).values('booked_by__first_name','booked_by__last_name','booked_by__username','booked_by__role').annotate(count=Count('id')).order_by('-count')[:5])
    context = {'date_from': date_from.isoformat(), 'date_to': date_to.isoformat(), 'facility_type_filter': facility_type_filter, 'floor_filter': floor_filter, 'floor_choices': Facility.FLOOR_CHOICES, 'type_choices': Facility.TYPE_CHOICES, 'total_bookings': total, 'approved': approved, 'pending': pending, 'rejected': rejected, 'cancelled': cancelled, 'approval_rate': approval_rate, 'total_facilities': Facility.objects.count(), 'active_facilities': Facility.objects.filter(status='active').count(), 'maintenance_facilities': Facility.objects.filter(status='maintenance').count(), 'total_users': User.objects.count(), 'status_chart_json': json.dumps({'labels': ['Approved','Pending','Rejected','Cancelled'], 'data': [approved,pending,rejected,cancelled], 'colors': ['#1a6b3a','#b7770d','#c0392b','#888780']}), 'daily_labels_json': json.dumps(daily_labels), 'daily_data_json': json.dumps(daily_data), 'hour_labels_json': json.dumps([f'{h}:00' for h in range(7,21)]), 'hour_data_json': json.dumps(hour_data), 'type_chart_json': json.dumps({'labels': [t['facility__facility_type'].replace('_',' ').title() for t in type_data], 'data': [t['count'] for t in type_data]}), 'floor_chart_json': json.dumps({'labels': [f['facility__floor'] or 'Unknown' for f in floor_data], 'data': [f['count'] for f in floor_data]}), 'facility_usage': facility_usage, 'top_bookers': top_bookers}
    return render(request, 'reports.html', context)


@login_required(login_url='login')
@reports_required
def reports_export_csv(request):
    date_from_str = request.GET.get('date_from', ''); date_to_str = request.GET.get('date_to', '')
    today = datetime.date.today()
    date_from = datetime.date.fromisoformat(date_from_str) if date_from_str else today - datetime.timedelta(days=30)
    date_to = datetime.date.fromisoformat(date_to_str) if date_to_str else today
    bookings = Booking.objects.filter(date__gte=date_from, date__lte=date_to).select_related('facility', 'booked_by', 'approved_by').order_by('date', 'start_time')
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="bookings_{date_from}_{date_to}.csv"'
    writer = csv.writer(response)
    writer.writerow(['ID','Facility','Floor','Type','Booked By','Role','Date','Start','End','Attendees','Purpose','Equipment Needed','Equipment Status','Status','Approved By'])
    for b in bookings:
        writer.writerow([b.pk, b.facility.name, b.facility.floor, b.facility.get_facility_type_display(), b.booked_by.get_full_name() or b.booked_by.username, b.booked_by.get_role_display(), b.date, b.start_time.strftime('%H:%M'), b.end_time.strftime('%H:%M'), b.number_of_attendees, b.purpose, b.equipment_needed, b.get_equipment_status_display() if b.equipment_needed else '', b.get_status_display(), b.approved_by.get_full_name() if b.approved_by else ''])
    return response