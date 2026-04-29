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
from .models import User, Facility, Booking, BookingGroup, Notification, Announcement, NotificationTemplate, ActivityLog, IssueReport, Message, SystemSetting

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
    if not request.user.can_edit_facility_details():
        messages.error(request, 'Technical staff cannot create facilities.')
        return redirect('facilities')
        
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
        if not request.user.can_edit_facility_details():
            # Technical Staff - Only allow status update
            new_status = request.POST.get('status')
            if new_status in dict(Facility.STATUS_CHOICES):
                facility.status = new_status
                facility.save()
                log_activity(request.user, 'edit_facility', f'Updated status for: {facility.name} to {new_status}', request)
                messages.success(request, f'Facility status for "{facility.name}" updated.')
            return redirect('facility_detail', pk=facility.pk)
            
        # Full edit for Superusers and Managers
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

def check_booking_conflict(facility, date, start_time, end_time, exclude_pk=None, exclude_group_pk=None):
    conflicts = Booking.objects.filter(facility=facility, date=date, status__in=[Booking.PENDING, Booking.APPROVED]).exclude(pk=exclude_pk or 0)
    if exclude_group_pk:
        conflicts = conflicts.exclude(group_id=exclude_group_pk)
    return [b for b in conflicts if b.start_time < end_time and b.end_time > start_time]


@login_required(login_url='login')
def bookings_view(request):
    view_mode = request.GET.get('view', 'list')
    status_filter = request.GET.get('status', '')
    facility_filter = request.GET.get('facility', '')
    limit_filter = request.GET.get('limit', '25')
    
    # Check if HTMX polling request
    is_htmx = request.headers.get('HX-Request') == 'true'
    last_created_at_str = request.GET.get('last_created_at')

    if request.user.can_manage_facilities():
        bookings_qs = Booking.objects.filter(group__isnull=True).select_related('facility', 'booked_by')
        groups_qs = BookingGroup.objects.select_related('facility', 'booked_by')
    else:
        bookings_qs = Booking.objects.filter(booked_by=request.user, group__isnull=True).select_related('facility')
        groups_qs = BookingGroup.objects.filter(booked_by=request.user).select_related('facility')
        
    if status_filter: 
        bookings_qs = bookings_qs.filter(status=status_filter)
        groups_qs = groups_qs.filter(status=status_filter)
    if facility_filter: 
        bookings_qs = bookings_qs.filter(facility_id=facility_filter)
        groups_qs = groups_qs.filter(facility_id=facility_filter)
        
    # For polling, only get newer ones
    if is_htmx and last_created_at_str:
        try:
            # Handle potential 'Z' suffix or other ISO formats
            dt_str = last_created_at_str.replace('Z', '+00:00')
            last_created_at = datetime.datetime.fromisoformat(dt_str)
            bookings_qs = bookings_qs.filter(created_at__gt=last_created_at)
            groups_qs = groups_qs.filter(created_at__gt=last_created_at)
        except ValueError:
            pass

    bookings_list = list(bookings_qs)
    groups_list = list(groups_qs)
    for g in groups_list:
        g.is_group = True
    
    combined_bookings = bookings_list + groups_list
    combined_bookings.sort(key=lambda x: x.created_at, reverse=True)

    # If polling, we just return the new rows
    if is_htmx and last_created_at_str:
        return render(request, 'partials/booking_rows.html', {
            'bookings': combined_bookings
        })

    if limit_filter and limit_filter != 'all':
        try:
            limit = int(limit_filter)
            combined_bookings = combined_bookings[:limit]
        except ValueError:
            pass

    today = datetime.date.today()
    cal = [{'id': b.pk, 'title': f'{b.facility.name} — {b.booked_by.get_full_name() or b.booked_by.username}', 'start': f'{b.date}T{b.start_time}', 'end': f'{b.date}T{b.end_time}', 'color': '#1a6b3a' if b.status == 'approved' else '#b7770d', 'status': b.status, 'facility': b.facility.name, 'booked_by': b.booked_by.get_full_name() or b.booked_by.username, 'purpose': b.purpose, 'is_own': b.booked_by_id == request.user.id} for b in Booking.objects.select_related('facility', 'booked_by').filter(status__in=['pending', 'approved'])]
    
    total = Booking.objects.filter(group__isnull=True).count() + BookingGroup.objects.count() if request.user.can_manage_facilities() else Booking.objects.filter(booked_by=request.user, group__isnull=True).count() + BookingGroup.objects.filter(booked_by=request.user).count()
    pending = Booking.objects.filter(status='pending', group__isnull=True).count() + BookingGroup.objects.filter(status='pending').count() if request.user.can_manage_facilities() else Booking.objects.filter(booked_by=request.user, status='pending', group__isnull=True).count() + BookingGroup.objects.filter(booked_by=request.user, status='pending').count()
    approved = Booking.objects.filter(status='approved', group__isnull=True).count() + BookingGroup.objects.filter(status='approved').count() if request.user.can_manage_facilities() else Booking.objects.filter(booked_by=request.user, status='approved', group__isnull=True).count() + BookingGroup.objects.filter(booked_by=request.user, status='approved').count()
    stats = {'total': total, 'pending': pending, 'approved': approved, 'today': Booking.objects.filter(date=today).count()}
    
    auto_approve, _ = SystemSetting.objects.get_or_create(key='auto_approve_enabled', defaults={'value': False})
    
    return render(request, 'bookings.html', {
        'bookings': combined_bookings, 
        'calendar_bookings_json': json.dumps(cal), 
        'view_mode': view_mode, 
        'status_filter': status_filter, 
        'facility_filter': facility_filter, 
        'limit_filter': limit_filter,
        'facilities': Facility.objects.filter(status='active'), 
        'stats': stats, 
        'today': today,
        'auto_approve_enabled': auto_approve.value
    })


@login_required(login_url='login')
def toggle_auto_approve_view(request):
    if not request.user.can_approve_bookings():
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            enabled = data.get('enabled', False)
            setting, _ = SystemSetting.objects.get_or_create(key='auto_approve_enabled')
            setting.value = enabled
            setting.updated_by = request.user
            setting.save()
            log_activity(request.user, 'edit_user', f'Toggled Auto-Approve: {"ON" if enabled else "OFF"}', request)
            return JsonResponse({'success': True, 'enabled': setting.value})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)


@login_required(login_url='login')
def booking_check_conflict(request):
    if request.method != 'POST': return JsonResponse({'error': 'POST required'}, status=405)
    try:
        data = json.loads(request.body)
        facility = Facility.objects.get(pk=data.get('facility_id'))
        is_recurring = data.get('is_recurring')
        start_time = datetime.time.fromisoformat(data.get('start_time'))
        end_time = datetime.time.fromisoformat(end_str := data.get('end_time'))
        
        now = timezone.localtime(timezone.now())
        if start_time >= end_time: return JsonResponse({'conflict': False, 'error': 'End time must be after start time.'})

        dates_to_check = []
        if is_recurring:
            start_date = datetime.date.fromisoformat(data.get('start_date'))
            end_date = datetime.date.fromisoformat(data.get('end_date'))
            day_of_week = int(data.get('day_of_week'))
            
            if start_date < now.date(): return JsonResponse({'conflict': False, 'error': 'Cannot book a past date.'})
            if end_date < start_date: return JsonResponse({'conflict': False, 'error': 'End date must be after start date.'})
            
            current_date = start_date
            while current_date <= end_date:
                if current_date.weekday() == day_of_week:
                    dates_to_check.append(current_date)
                current_date += datetime.timedelta(days=1)
                
            if not dates_to_check: return JsonResponse({'conflict': False, 'error': 'No matching days found in the given date range.'})
        else:
            date = datetime.date.fromisoformat(data.get('date'))
            if date < now.date(): return JsonResponse({'conflict': False, 'error': 'Cannot book a past date.'})
            dates_to_check.append(date)
        
        all_conflicts = []
        for date in dates_to_check:
            conflicts = check_booking_conflict(facility, date, start_time, end_time, data.get('exclude_pk'), data.get('exclude_group_pk'))
            all_conflicts.extend(conflicts)
            
        if all_conflicts:
            suggestions = []
            for offset in [1, 2, 3]:
                alt_start = (datetime.datetime.combine(dates_to_check[0], end_time) + datetime.timedelta(hours=offset)).time()
                duration = datetime.datetime.combine(dates_to_check[0], end_time) - datetime.datetime.combine(dates_to_check[0], start_time)
                alt_end = (datetime.datetime.combine(dates_to_check[0], alt_start) + duration).time()
                
                alt_conflict_found = False
                for date in dates_to_check:
                    if check_booking_conflict(facility, date, alt_start, alt_end, data.get('exclude_pk')):
                        alt_conflict_found = True
                        break
                        
                if not alt_conflict_found and alt_end.hour < 21:
                    suggestions.append({'start': str(alt_start)[:5], 'end': str(alt_end)[:5]})
                    if len(suggestions) >= 2: break
                    
            return JsonResponse({'conflict': True, 'conflicts': [{'booked_by': c.booked_by.get_full_name() or c.booked_by.username, 'date': str(c.date), 'start_time': str(c.start_time), 'end_time': str(c.end_time), 'status': c.get_status_display()} for c in all_conflicts], 'suggestions': suggestions})
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
        
        is_recurring = request.POST.get('is_recurring') == 'on'
        
        date_str = request.POST.get('date')
        start_date_str = request.POST.get('start_date')
        end_date_str = request.POST.get('end_date')
        day_of_week_str = request.POST.get('day_of_week')
        
        start_str = request.POST.get('start_time')
        end_str = request.POST.get('end_time')
        purpose = request.POST.get('purpose', '').strip()
        attendees = request.POST.get('number_of_attendees', 1)
        force = request.POST.get('force_submit') == '1'
        equipment_items = request.POST.getlist('equipment_items')
        equipment_needed = request.POST.get('equipment_needed', '').strip()
        all_eq = (', '.join(equipment_items) + (' | ' + equipment_needed if equipment_needed else '')) if equipment_items else equipment_needed

        if facility_id and start_str and end_str and ((not is_recurring and date_str) or (is_recurring and start_date_str and end_date_str and day_of_week_str)):
            facility = get_object_or_404(Facility, pk=facility_id)
            start_time = datetime.time.fromisoformat(start_str)
            end_time = datetime.time.fromisoformat(end_str)
            now = timezone.localtime(timezone.now())
            
            if start_time >= end_time:
                messages.error(request, 'End time must be after start time.')
            else:
                dates_to_book = []
                has_error = False
                
                if is_recurring:
                    start_date = datetime.date.fromisoformat(start_date_str)
                    end_date = datetime.date.fromisoformat(end_date_str)
                    day_of_week = int(day_of_week_str)
                    
                    if start_date < now.date(): 
                        messages.error(request, 'Cannot book a past date.')
                        has_error = True
                    elif end_date < start_date:
                        messages.error(request, 'End date must be after start date.')
                        has_error = True
                    else:
                        current_date = start_date
                        while current_date <= end_date:
                            if current_date.weekday() == day_of_week:
                                dates_to_book.append(current_date)
                            current_date += datetime.timedelta(days=1)
                        if not dates_to_book:
                            messages.error(request, 'No matching days found in the given date range.')
                            has_error = True
                else:
                    date = datetime.date.fromisoformat(date_str)
                    if date < now.date():
                        messages.error(request, 'Cannot book a past date.')
                        has_error = True
                    else:
                        dates_to_book.append(date)
                
                if not has_error:
                    all_conflicts = []
                    for d in dates_to_book:
                        all_conflicts.extend(check_booking_conflict(facility, d, start_time, end_time))
                        
                    if all_conflicts and not force:
                        return render(request, 'booking_form.html', {'facilities': facilities, 'has_conflict': True, 'conflict_info': all_conflicts, 'form_data': request.POST, 'preselect': facility_id, 'equipment_choices': EQUIPMENT_CHOICES, 'today': datetime.date.today().isoformat(), 'is_recurring': is_recurring})
                        
                    if is_recurring:
                        auto_approve_setting = SystemSetting.objects.filter(key='auto_approve_enabled', value=True).exists()
                        status = BookingGroup.APPROVED if auto_approve_setting else BookingGroup.PENDING
                        approved_by = request.user if auto_approve_setting else None
                        
                        group = BookingGroup.objects.create(
                            facility=facility, booked_by=request.user, day_of_week=day_of_week,
                            start_date=start_date, end_date=end_date, start_time=start_time, end_time=end_time,
                            purpose=purpose, number_of_attendees=attendees, equipment_needed=all_eq,
                            status=status, approved_by=approved_by
                        )
                        for d in dates_to_book:
                            Booking.objects.create(group=group, facility=facility, booked_by=request.user, date=d, start_time=start_time, end_time=end_time, purpose=purpose, number_of_attendees=attendees, equipment_needed=all_eq, status=status, approved_by=approved_by)
                        
                        if status == BookingGroup.APPROVED:
                            send_notification(request.user, 'approval', f'Recurring Booking Approved: {facility.name}', f'Your recurring booking for {facility.name} starting {start_date} has been automatically approved.', booking=None)
                        else:
                            send_notification(request.user, 'confirmation', f'Recurring Booking Submitted: {facility.name}', f'Your recurring booking for {facility.name} starting {start_date} is pending approval.', booking=None)
                            
                        if all_eq:
                            it_staff = User.objects.filter(role__in=[User.TECHNICAL_STAFF, User.FACILITY_MANAGER, User.SUPERUSER_ROLE])
                            for staff in it_staff:
                                Notification.objects.create(recipient=staff, notif_type='confirmation', title=f'Equipment Request: {facility.name} (Recurring)', message=f'{request.user.get_full_name() or request.user.username} needs: {all_eq}\nFor: {facility.name} recurring starting {start_date}', sent_by=request.user)
                        log_activity(request.user, 'book', f'Booked {facility.name} recurring starting {start_date} (Auto-Approved: {auto_approve_setting})', request)
                        messages.success(request, (f'Recurring booking auto-approved!' if auto_approve_setting else f'Recurring booking submitted!') + (f' Equipment request sent to IT Department.' if all_eq else ''))
                    else:
                        auto_approve_setting = SystemSetting.objects.filter(key='auto_approve_enabled', value=True).exists()
                        status = Booking.APPROVED if auto_approve_setting else Booking.PENDING
                        approved_by = request.user if auto_approve_setting else None
                        
                        booking = Booking.objects.create(facility=facility, booked_by=request.user, date=dates_to_book[0], start_time=start_time, end_time=end_time, purpose=purpose, number_of_attendees=attendees, equipment_needed=all_eq, status=status, approved_by=approved_by)
                        
                        if status == Booking.APPROVED:
                            send_notification(request.user, 'approval', f'Booking Approved: {facility.name}', f'Your booking for {facility.name} on {dates_to_book[0]} has been automatically approved.', booking=booking)
                        else:
                            send_notification(request.user, 'confirmation', f'Booking Submitted: {facility.name}', f'Your booking for {facility.name} on {dates_to_book[0]} is pending approval.', booking=booking)
                            
                        if all_eq:
                            it_staff = User.objects.filter(role__in=[User.TECHNICAL_STAFF, User.FACILITY_MANAGER, User.SUPERUSER_ROLE])
                            for staff in it_staff:
                                Notification.objects.create(recipient=staff, notif_type='confirmation', title=f'Equipment Request: {facility.name}', message=f'{request.user.get_full_name() or request.user.username} needs: {all_eq}\nFor: {facility.name} on {dates_to_book[0]} at {start_time.strftime("%I:%M %p")}', booking=booking, sent_by=request.user)
                        log_activity(request.user, 'book', f'Booked {facility.name} on {dates_to_book[0]} (Auto-Approved: {auto_approve_setting})', request)
                        messages.success(request, (f'Booking #{booking.pk} auto-approved!' if auto_approve_setting else f'Booking #{booking.pk} submitted!') + (f' Equipment request sent to IT Department.' if all_eq else ''))
                        
                    return redirect('bookings')
        else: messages.error(request, 'All required fields must be filled.')
    
    is_recurring_get = request.GET.get('recurring') == '1' or request.POST.get('is_recurring') == 'on'
    return render(request, 'booking_form.html', {'facilities': facilities, 'preselect': preselect_facility, 'today': datetime.date.today().isoformat(), 'equipment_choices': EQUIPMENT_CHOICES, 'is_recurring': is_recurring_get})


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
def booking_group_action_view(request, pk):
    group = get_object_or_404(BookingGroup, pk=pk)
    action = request.POST.get('action')
    if action in ['approve', 'reject']:
        if not request.user.can_approve_bookings():
            messages.error(request, 'Only Facility Managers can approve or reject bookings.')
            return redirect('bookings')
        
        if action == 'approve':
            group.status = BookingGroup.APPROVED; group.approved_by = request.user; group.save()
            group.bookings.update(status=Booking.APPROVED, approved_by=request.user)
            send_notification(group.booked_by, 'approval', f'Recurring Booking Approved: {group.facility.name}', '', sent_by=request.user)
            log_activity(request.user, 'approve', f'Approved Group #{pk}', request)
            messages.success(request, f'Recurring Booking #{pk} approved.')
        elif action == 'reject':
            group.status = BookingGroup.REJECTED; group.save()
            group.bookings.update(status=Booking.REJECTED)
            send_notification(group.booked_by, 'rejection', f'Recurring Booking Rejected: {group.facility.name}', '', sent_by=request.user)
            log_activity(request.user, 'reject', f'Rejected Group #{pk}', request)
            messages.warning(request, f'Recurring Booking #{pk} rejected.')
    return redirect('bookings')


@login_required(login_url='login')
def booking_group_cancel_view(request, pk):
    group = get_object_or_404(BookingGroup, pk=pk)
    if not (request.user == group.booked_by or request.user.can_manage_facilities()):
        messages.error(request, 'You do not have permission to cancel this.')
        return redirect('bookings')
        
    if group.status in [BookingGroup.PENDING, BookingGroup.APPROVED]:
        group.status = BookingGroup.CANCELLED; group.save()
        
        now = timezone.localtime(timezone.now())
        future_bookings = group.bookings.filter(date__gte=now.date(), status__in=[Booking.PENDING, Booking.APPROVED])
        for b in future_bookings:
            if b.date == now.date() and b.start_time < now.time():
                continue
            b.status = Booking.CANCELLED
            b.save()
            
        send_notification(group.booked_by, 'cancellation', f'Recurring Booking Cancelled: {group.facility.name}', '')
        log_activity(request.user, 'cancel', f'Cancelled Group #{pk}', request)
        messages.success(request, 'Recurring booking cancelled (future dates).')
    else: messages.error(request, 'Only pending or approved bookings can be cancelled.')
    return redirect('bookings')


@login_required(login_url='login')
def booking_group_edit_view(request, pk):
    group = get_object_or_404(BookingGroup, pk=pk)
    if not (request.user == group.booked_by or request.user.can_manage_facilities()):
        messages.error(request, 'You do not have permission to edit this booking.')
        return redirect('bookings')
        
    facilities = Facility.objects.filter(status='active')
    
    if request.method == 'POST':
        facility_id = request.POST.get('facility')
        
        start_date_str = request.POST.get('start_date')
        end_date_str = request.POST.get('end_date')
        day_of_week_str = request.POST.get('day_of_week')
        
        start_str = request.POST.get('start_time')
        end_str = request.POST.get('end_time')
        purpose = request.POST.get('purpose', '').strip()
        attendees = request.POST.get('number_of_attendees', 1)
        force = request.POST.get('force_submit') == '1'
        equipment_items = request.POST.getlist('equipment_items')
        equipment_needed = request.POST.get('equipment_needed', '').strip()
        all_eq = (', '.join(equipment_items) + (' | ' + equipment_needed if equipment_needed else '')) if equipment_items else equipment_needed

        if facility_id and start_str and end_str and start_date_str and end_date_str and day_of_week_str:
            facility = get_object_or_404(Facility, pk=facility_id)
            start_time = datetime.time.fromisoformat(start_str)
            end_time = datetime.time.fromisoformat(end_str)
            start_date = datetime.date.fromisoformat(start_date_str)
            end_date = datetime.date.fromisoformat(end_date_str)
            day_of_week = int(day_of_week_str)
            now = timezone.localtime(timezone.now())
            
            if start_time >= end_time:
                messages.error(request, 'End time must be after start time.')
            elif start_date < now.date() and start_date != group.start_date: 
                messages.error(request, 'Cannot change start date to a past date.')
            elif end_date < now.date() and end_date != group.end_date:
                messages.error(request, 'Cannot change end date to a past date.')
            elif end_date < start_date:
                messages.error(request, 'End date must be after start date.')
            else:
                dates_to_book = []
                current_date = max(start_date, now.date())
                while current_date <= end_date:
                    if current_date.weekday() == day_of_week:
                        if current_date == now.date() and start_time < now.time():
                            pass
                        else:
                            dates_to_book.append(current_date)
                    current_date += datetime.timedelta(days=1)
                
                all_conflicts = []
                for d in dates_to_book:
                    all_conflicts.extend(check_booking_conflict(facility, d, start_time, end_time, exclude_pk=None))
                    
                filtered_conflicts = [c for c in all_conflicts if c.group_id != group.pk]
                
                if filtered_conflicts and not force:
                    form_data = request.POST.copy()
                    form_data['equipment_items'] = equipment_items
                    return render(request, 'booking_form.html', {'facilities': facilities, 'has_conflict': True, 'conflict_info': filtered_conflicts, 'form_data': form_data, 'preselect': facility_id, 'equipment_choices': EQUIPMENT_CHOICES, 'today': datetime.date.today().isoformat(), 'is_recurring': True, 'is_edit': True})
                
                group.facility = facility
                group.start_date = start_date
                group.end_date = end_date
                group.start_time = start_time
                group.end_time = end_time
                group.day_of_week = day_of_week
                group.purpose = purpose
                group.number_of_attendees = attendees
                group.equipment_needed = all_eq
                group.save()
                
                future_bookings = group.bookings.filter(date__gte=now.date())
                for b in future_bookings:
                    if b.date == now.date() and b.start_time < now.time():
                        continue
                    b.delete()
                    
                for d in dates_to_book:
                    Booking.objects.create(group=group, facility=facility, booked_by=group.booked_by, date=d, start_time=start_time, end_time=end_time, purpose=purpose, number_of_attendees=attendees, equipment_needed=all_eq, status=group.status, approved_by=group.approved_by)
                
                messages.success(request, f'Recurring booking updated.')
                return redirect('bookings')
        else: messages.error(request, 'All required fields must be filled.')
    
    form_data = {
        'group_id': str(group.pk),
        'facility': str(group.facility.pk),
        'start_date': str(group.start_date),
        'end_date': str(group.end_date),
        'start_time': group.start_time.strftime('%H:%M'),
        'end_time': group.end_time.strftime('%H:%M'),
        'day_of_week': str(group.day_of_week),
        'purpose': group.purpose,
        'number_of_attendees': group.number_of_attendees,
        'equipment_needed': group.equipment_needed,
    }
    
    if group.equipment_needed:
        if '|' in group.equipment_needed:
            parts = group.equipment_needed.split('|', 1)
            eq_items = [x.strip() for x in parts[0].split(',') if x.strip()]
            form_data['equipment_needed'] = parts[1].strip()
        else:
            eq_items = [x.strip() for x in group.equipment_needed.split(',') if x.strip() in EQUIPMENT_CHOICES]
            form_data['equipment_needed'] = group.equipment_needed if not eq_items else ''
        form_data['equipment_items'] = eq_items
        
    return render(request, 'booking_form.html', {'facilities': facilities, 'today': datetime.date.today().isoformat(), 'equipment_choices': EQUIPMENT_CHOICES, 'is_recurring': True, 'form_data': form_data, 'is_edit': True})


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
            booking=booking,
            issue_report=issue
        )

    messages.success(request, 'Room change request submitted successfully.')
    return redirect('booking_detail', pk=pk)


# ── Issue Reports ──────────────────────────────────────────────

@login_required(login_url='login')
def issue_list_view(request):
    from django.db.models import Count, Q
    status_filter = request.GET.get('status', '')
    priority_filter = request.GET.get('priority', '')
    if request.user.is_it_staff():
        issues = IssueReport.objects.select_related('facility', 'reported_by').all()
    else:
        issues = IssueReport.objects.filter(reported_by=request.user).select_related('facility')

    # Annotate with unread message count and unread notification flag for the current user
    issues = issues.annotate(
        unread_count=Count(
            'messages',
            filter=Q(messages__recipient=request.user, messages__is_read=False)
        ),
        has_unread_notif=Count(
            'notifications',
            filter=Q(notifications__recipient=request.user, notifications__is_read=False)
        )
    )

    if status_filter: issues = issues.filter(status=status_filter)
    if priority_filter: issues = issues.filter(priority=priority_filter)
    
    # Ensure explicit ordering by latest first
    issues = issues.order_by('-created_at')
    
    return render(request, 'issue_list.html', {'issues': issues, 'status_filter': status_filter, 'priority_filter': priority_filter, 'status_choices': IssueReport.STATUS_CHOICES, 'priority_choices': IssueReport.PRIORITY_CHOICES})


@login_required(login_url='login')
def issue_report_create_view(request):
    if not request.user.can_report_issues():
        messages.error(request, 'Technical staff cannot report issues.')
        return redirect('issue_list')
        
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
            Notification.objects.create(recipient=staff, notif_type='announcement', title=notif_title, message=notif_msg, sent_by=request.user, issue_report=issue)

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
    
    # Mark notifications as read for the current user
    Notification.objects.filter(issue_report=issue, recipient=request.user, is_read=False).update(is_read=True)
    
    # Mark issue as read if staff is viewing it
    if request.user.is_it_staff() and not issue.is_read:
        issue.is_read = True
        issue.save()
    
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
        issue.is_read = True

        if issue.status == 'room_change_approved' and original_status != 'room_change_approved':
            issue.requesting_room_change = True  # Ensure consistency
            new_fac_id = request.POST.get('new_facility')
            if new_fac_id:
                new_fac = get_object_or_404(Facility, pk=new_fac_id)
                issue.room_change_new_facility = new_fac
                issue.room_change_approved_by = request.user
                # Update the related booking
                if issue.booking:
                    issue.booking.facility = new_fac
                    issue.booking.save()
                    Notification.objects.create(recipient=issue.reported_by, notif_type='approval', title=f'Room Change Approved: {new_fac.name}', message=f'Your room change request has been approved. Your booking has been moved to {new_fac.name}.', booking=issue.booking, sent_by=request.user, issue_report=issue)
                else:
                    Notification.objects.create(recipient=issue.reported_by, notif_type='approval', title=f'Room Change Approved: {new_fac.name}', message=f'Your room change request has been approved. New room: {new_fac.name}.', sent_by=request.user, issue_report=issue)
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
            Notification.objects.create(recipient=issue.reported_by, notif_type='confirmation', title=f'Issue Resolved: {issue.title}', message=f'Your reported issue "{issue.title}" in {issue.facility.name} has been resolved. {issue.resolution_notes}', sent_by=request.user, issue_report=issue)

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
                Notification.objects.create(recipient=recipient, notif_type='announcement', title=f'New message re: {issue.title}', message=f'{request.user.get_full_name() or request.user.username}: {body[:100]}', sent_by=request.user, issue_report=issue)
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


@login_required(login_url='login')
def settings_view(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'update_profile':
            request.user.department = request.POST.get('department', request.user.department)
            request.user.save()
            messages.success(request, 'Profile updated.')
            
        elif action == 'change_name':
            request.user.first_name = request.POST.get('first_name', '').strip()
            request.user.last_name = request.POST.get('last_name', '').strip()
            request.user.save()
            messages.success(request, 'Name updated successfully.')
            
        elif action == 'change_username':
            new_username = request.POST.get('username', '').strip()
            if new_username and new_username != request.user.username:
                if User.objects.filter(username=new_username).exists():
                    messages.error(request, 'Username already taken.')
                else:
                    request.user.username = new_username
                    request.user.save()
                    messages.success(request, 'Username changed successfully.')
            
        elif action == 'update_image':
            if 'profile_picture' in request.FILES:
                request.user.profile_picture = request.FILES['profile_picture']
                request.user.save()
                messages.success(request, 'Profile picture updated.')
                
        elif action == 'remove_image':
            request.user.profile_picture = None
            request.user.save()
            messages.success(request, 'Profile picture removed.')
            
        elif action == 'change_email':
            new_email = request.POST.get('email', '').strip()
            if new_email:
                request.user.email = new_email
                request.user.save()
                messages.success(request, 'Email updated successfully.')
                
        elif action == 'change_password':
            new_pw = request.POST.get('new_password')
            confirm_pw = request.POST.get('confirm_password')
            
            if new_pw != confirm_pw:
                messages.error(request, 'Passwords do not match.')
            elif len(new_pw) < 8:
                messages.error(request, 'New password must be at least 8 characters.')
            else:
                request.user.set_password(new_pw)
                request.user.save()
                update_session_auth_hash(request, request.user)
                messages.success(request, 'Password changed successfully.')
                
        elif action == 'toggle_mfa':
            request.user.mfa_enabled = not request.user.mfa_enabled
            request.user.save()
            messages.success(request, f'2-Step Verification {"enabled" if request.user.mfa_enabled else "disabled"}.')
            
        elif action == 'delete_account':
            confirmation = request.POST.get('confirmation', '').strip()
            if confirmation == "Yes, i want to delete my account":
                user_pk = request.user.pk
                username = request.user.username
                logout(request)
                User.objects.filter(pk=user_pk).delete()
                messages.success(request, f'Account "{username}" has been permanently deleted.')
                return redirect('login')
            else:
                messages.error(request, 'Incorrect confirmation sentence.')
                
        return redirect('settings')
        
    return render(request, 'settings.html', {
        'dept_choices': User.DEPARTMENT_CHOICES,
    })


# ── Module 4: Notifications ───────────────────────────────────

@login_required(login_url='login')
def notifications_view(request):
    filter_type = request.GET.get('type', '')
    notifs = request.user.notifications.all()
    if filter_type: notifs = notifs.filter(notif_type=filter_type)
    request.user.notifications.filter(is_read=False).update(is_read=True)
    
    context = {'notifications': notifs, 'filter_type': filter_type, 'type_choices': Notification.TYPE_CHOICES, 'announcements': Announcement.objects.all()[:5]}
    
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'partials/notification_list.html', context)
        
    return render(request, 'notifications.html', context)


@login_required(login_url='login')
def notification_badges_view(request):
    return render(request, 'partials/notification_badges.html')


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