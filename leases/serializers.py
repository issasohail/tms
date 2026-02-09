# leases/serializers.py
from rest_framework import serializers
from .models_pcr import PropertyConditionReport, PCRPhoto
from .models_pcr_video import PCRVideo


class PCRPhotoSerializer(serializers.ModelSerializer):
    class Meta:
        model = PCRPhoto
        fields = ["id", "pcr", "image", "thumbnail", "taken_at",
                  "comment", "room", "order", "created_at"]
        read_only_fields = ["thumbnail", "taken_at", "created_at"]


class PCRVideoSerializer(serializers.ModelSerializer):
    play_url = serializers.SerializerMethodField()

    class Meta:
        model = PCRVideo
        fields = ["id", "pcr", "file", "poster", "encoded_mp4", "play_url",
                  "comment", "room", "order", "taken_at", "duration_seconds", "width", "height",
                  "sha256", "processing_status", "created_at", "updated_at"]
        read_only_fields = ["poster", "encoded_mp4", "taken_at", "duration_seconds", "width", "height",
                            "sha256", "processing_status", "created_at", "updated_at", "play_url"]

    def get_play_url(self, obj):
        f = obj.encoded_mp4 or obj.file
        try:
            return f.url
        except Exception:
            return None


class PCRSerializer(serializers.ModelSerializer):
    photos = PCRPhotoSerializer(many=True, read_only=True)
    videos = PCRVideoSerializer(many=True, read_only=True)

    class Meta:
        model = PropertyConditionReport
        fields = ["id", "lease", "title",
                  "created_at", "locked", "photos", "videos"]
        read_only_fields = ["created_at"]
