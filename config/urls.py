from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from allauth.socialaccount.views import ConnectionsView
from django.urls import reverse_lazy

class CustomConnectionsView(ConnectionsView):
    def get_success_url(self):
        # Definitively force redirect back to settings after successful disconnect
        return reverse_lazy('settings')
        
    def get(self, request, *args, **kwargs):
        # Prevent users from ever seeing the raw 3rdparty page on GET
        from django.shortcuts import redirect
        return redirect('settings')
        
    def dispatch(self, request, *args, **kwargs):
        if request.method == 'GET':
             from django.shortcuts import redirect
             return redirect('settings')
        return super().dispatch(request, *args, **kwargs)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/3rdparty/', CustomConnectionsView.as_view(), name='socialaccount_connections'),
    path('accounts/', include('allauth.urls')),
    path('', include('apps.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)