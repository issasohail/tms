# leases/storage.py
from django.core.files.storage import FileSystemStorage


class OverwriteStorage(FileSystemStorage):
    def get_available_name(self, name, max_length=None):
        """Returns the filename instead of generating a new one"""
        # If file exists, delete it to allow overwrite
        if self.exists(name):
            self.delete(name)
        return name
