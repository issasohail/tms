from datetime import datetime, time
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from smart_meter.models import Meter, MeterCommand, MeterTimingEvent
from smart_meter.services.command_lifecycle import revalidate_command
from smart_meter.services.timing_schedule import copy_timing_schedule, enforce_meter_timing_schedule, schedule_allows_power

class MeterTimingScheduleTests(TestCase):
    def setUp(self): self.meter=Meter.objects.create(meter_number='260821000001')
    def _aware(self,y,m,d,h,minute=0): return timezone.make_aware(datetime(y,m,d,h,minute), timezone.get_current_timezone())
    def test_no_schedule_does_not_gate_meter(self): self.assertIsNone(schedule_allows_power(self.meter,at=self._aware(2026,8,24,9)))
    def test_multiple_on_off_events_same_day(self):
        for h,c in [(6,'on'),(8,'off'),(18,'on'),(23,'off')]: MeterTimingEvent.objects.create(meter=self.meter,weekday=0,event_time=time(h),command=c)
        self.assertTrue(schedule_allows_power(self.meter,at=self._aware(2026,8,24,7)))
        self.assertFalse(schedule_allows_power(self.meter,at=self._aware(2026,8,24,10)))
        self.assertTrue(schedule_allows_power(self.meter,at=self._aware(2026,8,24,20)))
    @patch('smart_meter.services.timing_schedule.queue_relay_command')
    def test_off_event_queues_off(self,queue):
        MeterTimingEvent.objects.create(meter=self.meter,weekday=0,event_time=time(8),command='off')
        with patch('smart_meter.services.timing_schedule.timezone.now',return_value=self._aware(2026,8,24,9)): enforce_meter_timing_schedule(self.meter)
        queue.assert_called_once(); self.assertEqual(queue.call_args.args[1],'off'); self.assertEqual(queue.call_args.kwargs['source'],'schedule')
    def test_schedule_on_requires_proven_schedule_off(self):
        MeterTimingEvent.objects.create(meter=self.meter,weekday=0,event_time=time(8),command='on'); self.meter.power_status='off'; self.meter.save(update_fields=['power_status'])
        cmd=MeterCommand.objects.create(meter=self.meter,meter_number=self.meter.meter_number,frame_hex='00',command_type='relay',desired_state='on',source='schedule')
        with patch('smart_meter.services.timing_schedule.timezone.now',return_value=self._aware(2026,8,24,9)): result=revalidate_command(cmd)
        self.assertFalse(result.allowed); self.assertIn('no proven schedule OFF',result.reason)
    def test_automatic_restore_is_blocked_when_latest_event_is_off(self):
        MeterTimingEvent.objects.create(meter=self.meter,weekday=0,event_time=time(8),command='off')
        cmd=MeterCommand.objects.create(meter=self.meter,meter_number=self.meter.meter_number,frame_hex='00',command_type='relay',desired_state='on',source='payment')
        with patch('smart_meter.services.timing_schedule.timezone.now',return_value=self._aware(2026,8,24,9)): result=revalidate_command(cmd)
        self.assertFalse(result.allowed); self.assertEqual(result.reason,'timing schedule blocks reconnection')
    def test_copy_replaces_target_schedule(self):
        target=Meter.objects.create(meter_number='260821000002'); MeterTimingEvent.objects.create(meter=self.meter,weekday=1,event_time=time(9),command='on'); MeterTimingEvent.objects.create(meter=target,weekday=2,event_time=time(1),command='off')
        self.assertEqual(copy_timing_schedule(self.meter,target),1); row=target.timing_events.get(); self.assertEqual((row.weekday,row.event_time,row.command),(1,time(9),'on'))
