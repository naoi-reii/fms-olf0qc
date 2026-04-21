from django.core.management.base import BaseCommand
from apps.models import Facility, User


class Command(BaseCommand):
    help = 'Seed CAS Building facilities'

    def handle(self, *args, **kwargs):

        # Get first superuser as created_by
        admin = User.objects.filter(is_superuser=True).first()

        facilities = [
            # 2nd Floor - Classrooms 201-208
            {'name': 'Room 201', 'facility_type': 'classroom', 'location': 'CAS Building, 2nd Floor', 'capacity': 40, 'tags': 'classroom'},
            {'name': 'Room 202', 'facility_type': 'classroom', 'location': 'CAS Building, 2nd Floor', 'capacity': 40, 'tags': 'classroom'},
            {'name': 'Room 203', 'facility_type': 'classroom', 'location': 'CAS Building, 2nd Floor', 'capacity': 40, 'tags': 'classroom'},
            {'name': 'Room 204', 'facility_type': 'classroom', 'location': 'CAS Building, 2nd Floor', 'capacity': 40, 'tags': 'classroom'},
            {'name': 'Room 205', 'facility_type': 'classroom', 'location': 'CAS Building, 2nd Floor', 'capacity': 40, 'tags': 'classroom'},
            {'name': 'Room 206', 'facility_type': 'classroom', 'location': 'CAS Building, 2nd Floor', 'capacity': 40, 'tags': 'classroom'},
            {'name': 'Room 207', 'facility_type': 'classroom', 'location': 'CAS Building, 2nd Floor', 'capacity': 40, 'tags': 'classroom'},
            {'name': 'Room 208', 'facility_type': 'classroom', 'location': 'CAS Building, 2nd Floor', 'capacity': 40, 'tags': 'classroom'},

            # 3rd Floor - Computer Labs
            {'name': 'Acer Lab 1', 'facility_type': 'laboratory', 'location': 'CAS Building, 3rd Floor', 'capacity': 40, 'tags': 'computer lab, acer'},
            {'name': 'Acer Lab 2', 'facility_type': 'laboratory', 'location': 'CAS Building, 3rd Floor', 'capacity': 40, 'tags': 'computer lab, acer'},
            {'name': 'CLA',        'facility_type': 'laboratory', 'location': 'CAS Building, 3rd Floor', 'capacity': 40, 'tags': 'computer lab'},
            {'name': 'CLB',        'facility_type': 'laboratory', 'location': 'CAS Building, 3rd Floor', 'capacity': 40, 'tags': 'computer lab'},
            {'name': 'DTL',        'facility_type': 'laboratory', 'location': 'CAS Building, 3rd Floor', 'capacity': 40, 'tags': 'digital technology lab'},

            # 4th Floor - Mac & Network Labs
            {'name': 'Mac Lab',    'facility_type': 'laboratory', 'location': 'CAS Building, 4th Floor', 'capacity': 30, 'tags': 'mac lab, apple'},
            {'name': 'CNL A',      'facility_type': 'laboratory', 'location': 'CAS Building, 4th Floor', 'capacity': 40, 'tags': 'computer network lab'},
            {'name': 'CNL B',      'facility_type': 'laboratory', 'location': 'CAS Building, 4th Floor', 'capacity': 40, 'tags': 'computer network lab'},
        ]

        created = 0
        skipped = 0

        for f in facilities:
            obj, was_created = Facility.objects.get_or_create(
                name=f['name'],
                location=f['location'],
                defaults={
                    'facility_type': f['facility_type'],
                    'capacity': f['capacity'],
                    'tags': f['tags'],
                    'status': 'active',
                    'created_by': admin,
                }
            )
            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f"  ✓ Created: {f['name']} — {f['location']}"))
            else:
                skipped += 1
                self.stdout.write(self.style.WARNING(f"  – Skipped (exists): {f['name']}"))

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'Done! {created} created, {skipped} skipped.'))
        