from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.shortcuts import redirect, reverse
from django.contrib import messages
from allauth.core.exceptions import ImmediateHttpResponse

class MySocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin):
        """
        Strictly disable signup via social accounts.
        Only existing users who link their account in settings can use Google Login.
        """
        return False

    def get_connect_redirect_url(self, request, socialaccount):
        """
        Force redirect back to the settings page after connecting or disconnecting.
        """
        return reverse('settings')

    def pre_social_login(self, request, sociallogin):
        """
        Invoked just after a user successfully authenticates via a social provider,
        but before the login is actually processed.
        """
        # Scenario 1: User is already logged in (they are linking an account from Settings)
        if request.user.is_authenticated:
            pass # Allow the normal flow to link the account
            
        # Scenario 2: User is logged OUT (they clicked "Sign in with Google" on the login page)
        else:
            # Check if this Google account is already linked to a Django user
            if not sociallogin.is_existing:
                # It is NOT linked. We strictly reject this to prevent the callback hang.
                messages.error(
                    request,
                    "This Google account is not linked to any profile. "
                    "Please log in with your credentials and link your account in Settings first."
                )
                raise ImmediateHttpResponse(redirect('login'))

    def authentication_error(self, request, provider_id, error=None, exception=None, extra_context=None):
        """
        Handle authentication errors, including the case where a user is not found.
        """
        messages.error(
            request, 
            "This Google account is not linked to any student/staff profile. "
            "Please log in with your credentials and link your account in Settings."
        )
        raise ImmediateHttpResponse(redirect('login'))
