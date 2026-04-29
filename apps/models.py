from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    SUPERUSER_ROLE = 'superuser'
    FACILITY_MANAGER = 'facility_manager'
    TECHNICAL_STAFF = 'technical_staff'
    STANDARD_USER = 'standard_user'

    ROLE_CHOICES = [
        (SUPERUSER_ROLE, 'Superuser'),
        (FACILITY_MANAGER, 'Facility Manager'),
        (TECHNICAL_STAFF, 'Technical Staff'),
        (STANDARD_USER, 'Standard User'),
    ]

    DEPARTMENT_CHOICES = [
        ('BSIT', 'BS Information Technology'),
        ('BSCS', 'BS Computer Science'),
        ('BSIS', 'BS Information Systems'),
        ('IT_DEPT', 'IT Department'),
        ('ADMIN', 'Administration'),
        ('OTHER', 'Other'),
    ]

    role = models.CharField(max_length=30, choices=ROLE_CHOICES, default=STANDARD_USER)
    department = models.CharField(max_length=30, choices=DEPARTMENT_CHOICES, blank=True, default='')
    phone = models.CharField(max_length=20, blank=True)
    profile_picture = models.ImageField(upload_to='profiles/', blank=True, null=True)
    profile_notes = models.TextField(blank=True)
    last_active = models.DateTimeField(null=True, blank=True)
    mfa_enabled = models.BooleanField(default=False)
    failed_login_attempts = models.PositiveIntegerField(default=0)
    is_locked = models.BooleanField(default=False)

    def can_manage_facilities(self):
        return self.role in [self.SUPERUSER_ROLE, self.FACILITY_MANAGER, self.TECHNICAL_STAFF] or self.is_superuser

    def can_edit_facility_details(self):
        return self.role in [self.SUPERUSER_ROLE, self.FACILITY_MANAGER] or self.is_superuser

    def can_approve_bookings(self):
        return self.role in [self.SUPERUSER_ROLE, self.FACILITY_MANAGER] or self.is_superuser

    def can_book(self):
        return self.role in [self.SUPERUSER_ROLE, self.FACILITY_MANAGER, self.STANDARD_USER] or self.is_superuser

    def can_manage_users(self):
        return self.role == self.SUPERUSER_ROLE or self.is_superuser

    def can_view_reports(self):
        return self.role in [self.SUPERUSER_ROLE, self.FACILITY_MANAGER] or self.is_superuser

    def can_manage_reports(self):
        return self.role in [self.SUPERUSER_ROLE, self.TECHNICAL_STAFF] or self.is_superuser

    def can_send_announcements(self):
        return self.role in [self.SUPERUSER_ROLE, self.FACILITY_MANAGER] or self.is_superuser

    def can_report_issues(self):
        return self.role != self.TECHNICAL_STAFF or self.is_superuser

    def is_it_staff(self):
        return self.role in [self.SUPERUSER_ROLE, self.FACILITY_MANAGER, self.TECHNICAL_STAFF] or self.is_superuser

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"


class Facility(models.Model):
    STATUS_CHOICES = [('active','Active'),('maintenance','Under Maintenance'),('unavailable','Unavailable')]
    TYPE_CHOICES = [('laboratory','Laboratory'),('classroom','Classroom'),('conference','Conference Room'),('auditorium','Auditorium'),('other','Other')]
    FLOOR_CHOICES = [('1st Floor','1st Floor'),('2nd Floor','2nd Floor'),('3rd Floor','3rd Floor'),('4th Floor','4th Floor'),('5th Floor','5th Floor'),('Other','Other')]

    name = models.CharField(max_length=100)
    facility_type = models.CharField(max_length=30, choices=TYPE_CHOICES, default='classroom')
    building = models.CharField(max_length=100, blank=True, default='CAS Building')
    floor = models.CharField(max_length=20, choices=FLOOR_CHOICES, blank=True)
    location = models.CharField(max_length=200)
    capacity = models.PositiveIntegerField(default=0)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    tags = models.CharField(max_length=200, blank=True)
    image = models.ImageField(upload_to='facilities/', blank=True, null=True)
    floor_plan = models.ImageField(upload_to='floor_plans/', blank=True, null=True)
    custodian = models.CharField(max_length=100, blank=True)
    availability_start = models.TimeField(null=True, blank=True)
    availability_end = models.TimeField(null=True, blank=True)
    is_restricted = models.BooleanField(default=False)
    allowed_roles = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='facilities_created')

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'Facilities'
        ordering = ['building', 'floor', 'name']


class BookingGroup(models.Model):
    PENDING = 'pending'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    CANCELLED = 'cancelled'
    STATUS_CHOICES = [(PENDING,'Pending'),(APPROVED,'Approved'),(REJECTED,'Rejected'),(CANCELLED,'Cancelled')]

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='booking_groups')
    booked_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='booking_groups')
    day_of_week = models.IntegerField() # 0 = Monday, 6 = Sunday
    start_date = models.DateField()
    end_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    purpose = models.TextField(blank=True)
    number_of_attendees = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_booking_groups')
    equipment_needed = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Group: {self.facility.name} — {self.get_day_of_week_display()}s ({self.start_date} to {self.end_date})"
        
    def get_day_of_week_display(self):
        days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        return days[self.day_of_week] if 0 <= self.day_of_week <= 6 else 'Unknown'

    class Meta:
        ordering = ['-created_at']


class Booking(models.Model):
    PENDING = 'pending'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    CANCELLED = 'cancelled'
    STATUS_CHOICES = [(PENDING,'Pending'),(APPROVED,'Approved'),(REJECTED,'Rejected'),(CANCELLED,'Cancelled')]

    group = models.ForeignKey(BookingGroup, on_delete=models.CASCADE, null=True, blank=True, related_name='bookings')
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='bookings')
    booked_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bookings')
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    purpose = models.TextField(blank=True)
    number_of_attendees = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_bookings')

    # Equipment requests
    equipment_needed = models.TextField(blank=True, help_text='List of equipment needed e.g. HDMI, remote, projector')
    equipment_status = models.CharField(max_length=20, choices=[('pending','Pending'),('prepared','Prepared'),('delivered','Delivered')], default='pending', blank=True)
    equipment_notes = models.TextField(blank=True, help_text='IT staff notes on equipment')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.facility.name} — {self.booked_by.get_full_name() or self.booked_by.username} ({self.date})"

    class Meta:
        ordering = ['-created_at']


class IssueReport(models.Model):
    """Faculty can report problems in a facility."""
    PRIORITY_CHOICES = [('low','Low'),('medium','Medium'),('high','High'),('urgent','Urgent')]
    STATUS_CHOICES = [
        ('open','Open'),
        ('in_progress','In Progress'),
        ('resolved','Resolved'),
        ('room_change_requested','Room Change Requested'),
        ('room_change_approved','Room Change Approved'),
    ]
    CATEGORY_CHOICES = [
        ('aircon','Air Conditioning'),
        ('internet','Internet / Network'),
        ('projector','Projector'),
        ('electrical','Electrical'),
        ('furniture','Furniture'),
        ('computer','Computer / Equipment'),
        ('other','Other'),
    ]

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='issue_reports')
    reported_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='issue_reports')
    booking = models.ForeignKey(Booking, on_delete=models.SET_NULL, null=True, blank=True, related_name='issue_reports')
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='other')
    title = models.CharField(max_length=200)
    description = models.TextField()
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='open')
    is_read = models.BooleanField(default=False)

    # Room change request
    requesting_room_change = models.BooleanField(default=False)
    preferred_alternative = models.ForeignKey(Facility, on_delete=models.SET_NULL, null=True, blank=True, related_name='change_requests')
    room_change_approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_room_changes')
    room_change_new_facility = models.ForeignKey(Facility, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_changes')

    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_issues')
    resolution_notes = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"[{self.get_priority_display()}] {self.title} — {self.facility.name}"

    class Meta:
        ordering = ['-created_at']


class Message(models.Model):
    """Direct messaging between IT staff and users."""
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_messages')
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_messages')
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    is_read = models.BooleanField(default=False)
    issue_report = models.ForeignKey(IssueReport, on_delete=models.SET_NULL, null=True, blank=True, related_name='messages')
    booking = models.ForeignKey(Booking, on_delete=models.SET_NULL, null=True, blank=True, related_name='messages')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.sender.username} → {self.recipient.username}: {self.subject}"

    class Meta:
        ordering = ['-created_at']


class NotificationTemplate(models.Model):
    TYPE_CHOICES = [
        ('confirmation','Booking Confirmation'),('approval','Booking Approved'),
        ('rejection','Booking Rejected'),('reminder','Booking Reminder'),
        ('cancellation','Booking Cancelled'),('announcement','Announcement'),
        ('escalation','Escalation Alert'),
    ]
    notif_type = models.CharField(max_length=20, choices=TYPE_CHOICES, unique=True)
    subject = models.CharField(max_length=200)
    body = models.TextField()
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='templates_updated')

    def render(self, context):
        try:
            return self.body.format(**context)
        except KeyError:
            return self.body

    def __str__(self):
        return f"Template: {self.get_notif_type_display()}"


class Notification(models.Model):
    TYPE_CHOICES = [
        ('confirmation','Booking Confirmation'),('approval','Booking Approved'),
        ('rejection','Booking Rejected'),('reminder','Booking Reminder'),
        ('cancellation','Booking Cancelled'),('announcement','Announcement'),
        ('escalation','Escalation Alert'),
    ]
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    notif_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    title = models.CharField(max_length=200)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    booking = models.ForeignKey(Booking, on_delete=models.SET_NULL, null=True, blank=True, related_name='notifications')
    issue_report = models.ForeignKey('IssueReport', on_delete=models.SET_NULL, null=True, blank=True, related_name='notifications')
    sent_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='sent_notifications')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class Announcement(models.Model):
    PRIORITY_CHOICES = [('low','Low'),('normal','Normal'),('high','High'),('urgent','Urgent')]
    title = models.CharField(max_length=200)
    message = models.TextField()
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='normal')
    target_roles = models.CharField(max_length=200, blank=True)
    sent_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='announcements_sent')
    recipient_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class ActivityLog(models.Model):
    ACTION_CHOICES = [
        ('login','Logged In'),('logout','Logged Out'),('book','Created Booking'),
        ('cancel','Cancelled Booking'),('create_facility','Created Facility'),
        ('edit_facility','Edited Facility'),('approve','Approved Booking'),
        ('reject','Rejected Booking'),('create_user','Created User'),('edit_user','Edited User'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='activity_logs')
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    description = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class SystemSetting(models.Model):
    key = models.CharField(max_length=50, unique=True)
    value = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return f"{self.key}: {self.value}"