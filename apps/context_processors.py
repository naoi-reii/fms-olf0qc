from .models import Booking, IssueReport

def notification_counts(request):
    if not request.user.is_authenticated:
        return {}
    
    if request.user.can_manage_facilities():
        pending_bookings = Booking.objects.filter(status='pending').count()
    else:
        pending_bookings = Booking.objects.filter(booked_by=request.user, status='pending').count()

    return {
        'unread_notifications': request.user.notifications.filter(is_read=False).count(),
        'pending_bookings': pending_bookings,
        'open_issues': IssueReport.objects.filter(status='open').count() if request.user.is_it_staff() else IssueReport.objects.filter(reported_by=request.user, status='open').count(),
        'unread_issue_messages': request.user.received_messages.filter(is_read=False, issue_report__isnull=False).count(),
    }
