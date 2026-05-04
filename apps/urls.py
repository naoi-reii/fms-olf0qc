from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('verify-2fa/', views.verify_2fa_view, name='verify_2fa'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/', views.profile_view, name='profile'),
    path('settings/', views.settings_view, name='settings'),
    path('', views.dashboard_view, name='dashboard'),

    # Module 1: Facility Management
    path('facilities/', views.facilities_view, name='facilities'),
    path('facilities/create/', views.facility_create_view, name='facility_create'),
    path('facilities/<int:pk>/', views.facility_detail_view, name='facility_detail'),
    path('facilities/<int:pk>/edit/', views.facility_edit_view, name='facility_edit'),
    path('facilities/<int:pk>/set-status/', views.facility_set_maintenance_view, name='facility_set_maintenance'),

    # Room Schedule
    path('schedule/', views.room_schedule_view, name='room_schedule'),

    # Module 2: Booking
    path('bookings/', views.bookings_view, name='bookings'),
    path('api/available-rooms/<int:booking_id>/', views.api_get_available_rooms, name='api_get_available_rooms'),
    path('bookings/create/', views.booking_create_view, name='booking_create'),
    path('bookings/<int:pk>/', views.booking_detail_view, name='booking_detail'),
    path('bookings/<int:pk>/action/', views.booking_approve_view, name='booking_action'),
    path('bookings/<int:pk>/cancel/', views.booking_cancel_view, name='booking_cancel'),
    path('bookings/toggle-auto-approve/', views.toggle_auto_approve_view, name='toggle_auto_approve'),
    path('bookings/group/<int:pk>/edit/', views.booking_group_edit_view, name='booking_group_edit'),
    path('bookings/group/<int:pk>/action/', views.booking_group_action_view, name='booking_group_action'),
    path('bookings/group/<int:pk>/cancel/', views.booking_group_cancel_view, name='booking_group_cancel'),
    path('bookings/<int:pk>/request-change/', views.booking_request_change_view, name='booking_request_change'),
    path('bookings/check-conflict/', views.booking_check_conflict, name='booking_check_conflict'),

    # Issue Reports
    path('issues/', views.issue_list_view, name='issue_list'),
    path('issues/create/', views.issue_report_create_view, name='issue_report_create'),
    path('issues/<int:pk>/', views.issue_detail_view, name='issue_detail'),
    path('issues/<int:pk>/update/', views.issue_update_view, name='issue_update'),
    path('issues/<int:issue_pk>/message/', views.send_message_view, name='send_message'),

    # Module 3: User Management
    path('users/', views.user_list_view, name='user_list'),
    path('users/create/', views.user_create_view, name='user_create'),
    path('users/<int:pk>/edit/', views.user_edit_view, name='user_edit'),
    path('users/<int:pk>/toggle-lock/', views.user_toggle_lock_view, name='user_toggle_lock'),
    path('users/<int:pk>/activity/', views.user_activity_view, name='user_activity'),

    # Module 4: Notifications
    path('notifications/', views.notifications_view, name='notifications'),
    path('notifications/mark-all-read/', views.notifications_mark_all_read_view, name='notifications_mark_all_read'),
    path('notifications/badges/', views.notification_badges_view, name='notification_badges'),
    path('notifications/<int:pk>/delete/', views.notification_delete, name='notification_delete'),
    path('announcements/', views.announcement_list_view, name='announcement_list'),
    path('announcements/create/', views.announcement_create_view, name='announcement_create'),
    path('notifications/templates/', views.template_list_view, name='notification_templates'),
    path('notifications/templates/<int:pk>/edit/', views.template_edit_view, name='template_edit'),

    # Module 5: Reports
    path('reports/', views.reports_view, name='reports'),
    path('reports/export/csv/', views.reports_export_csv, name='reports_export_csv'),
]