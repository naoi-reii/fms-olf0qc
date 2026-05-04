from .models import Booking, IssueReport, User
from django.db.models import Q

def notification_counts(request):
    if not request.user.is_authenticated:
        return {}
    
    # Base querysets
    notifs = request.user.notifications.all()
    if request.user.can_manage_facilities():
        bookings = Booking.objects.filter(status='pending')
    else:
        bookings = Booking.objects.filter(booked_by=request.user, status='pending')

    # Apply department restrictions for Facility Managers
    if request.user.role == User.FACILITY_MANAGER:
        # Restriction for bookings
        dept_filter = Q(booked_by__role=User.STANDARD_USER, booked_by__department=request.user.department) | ~Q(booked_by__role=User.STANDARD_USER)
        bookings = bookings.filter(dept_filter)
        
        # Restriction for notifications
        notifs = notifs.exclude(Q(sent_by__role=User.STANDARD_USER) & ~Q(sent_by__department=request.user.department))

    # Sync with notification indicator: count only UNREAD items for the specific user
    unread_issue_notifs = notifs.filter(is_read=False, issue_report__isnull=False).count()

    return {
        'unread_notifications': notifs.filter(is_read=False).count(),
        'pending_bookings': bookings.count(),
        'open_issues': unread_issue_notifs,
        'unread_issue_messages': request.user.received_messages.filter(is_read=False, issue_report__isnull=False).count(),
    }
