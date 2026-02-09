from django.http import HttpResponse


class NotificationRecursionMiddleware:
    """Middleware specifically for preventing notification recursion"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Skip if not a notification URL
        if not request.path.startswith('/notifications/'):
            return self.get_response(request)

        # Block recursion
        if hasattr(request, '_processing_notification'):
            return HttpResponse(
                "Notification recursion blocked by middleware",
                status=400
            )

        # Mark request as being processed
        request._processing_notification = True

        try:
            response = self.get_response(request)
        finally:
            # Clean up
            del request._processing_notification

        return response
