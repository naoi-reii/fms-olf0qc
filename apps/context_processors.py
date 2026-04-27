from .models import Booking, IssueReport

def notification_counts(request):
    if not request.user.is_authenticated:
        return {}
    
    if request.user.can_manage_facilities():
        pending_bookings = Booking.objects.filter(status='pending').count()
    else:
        pending_bookings = Booking.objects.filter(booked_by=request.user, status='pending').count()

    # Sync with notification indicator: count only UNREAD items for the specific user
    # For IT staff: unread reports they are notified about
    # For Standard users: unread status updates on their own reports
    unread_issue_notifs = request.user.notifications.filter(is_read=False, issue_report__isnull=False).count()

    return {
        'unread_notifications': request.user.notifications.filter(is_read=False).count(),
        'pending_bookings': pending_bookings,
        'open_issues': unread_issue_notifs,
        'unread_issue_messages': request.user.received_messages.filter(is_read=False, issue_report__isnull=False).count(),
    }
