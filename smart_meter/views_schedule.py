import json
from collections import defaultdict
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from smart_meter.models import Meter, MeterTimingEvent
from smart_meter.services.timing_schedule import copy_timing_schedule, next_schedule_event, schedule_allows_power

DAYS=[(0,'Mon'),(1,'Tue'),(2,'Wed'),(3,'Thu'),(4,'Fri'),(5,'Sat'),(6,'Sun')]


def _group_events(meter):
    groups={}
    for e in meter.timing_events.all():
        key=(e.event_time.strftime('%H:%M'),e.command,e.notes,e.is_enabled)
        groups.setdefault(key,[]).append(e.weekday)
    return [dict(time=k[0],command=k[1],notes=k[2],enabled=k[3],days=sorted(v)) for k,v in groups.items()]


def _save_json(meter, raw):
    try: data=json.loads(raw or '[]')
    except json.JSONDecodeError: raise ValueError('Invalid schedule data.')
    rows=[]; seen=set()
    for item in data:
        try: t=datetime.strptime(item['time'],'%H:%M').time()
        except Exception: raise ValueError('Every schedule event needs a valid HH:MM time.')
        cmd=item.get('command')
        if cmd not in {'on','off'}: raise ValueError('Command must be ON or OFF.')
        days=[int(x) for x in item.get('days',[]) if str(x).isdigit() and 0<=int(x)<=6]
        if not days: raise ValueError('Each schedule event needs at least one day.')
        for d in days:
            key=(d,t)
            if key in seen: raise ValueError(f'Duplicate event for {DAYS[d][1]} at {t:%H:%M}.')
            seen.add(key); rows.append(MeterTimingEvent(meter=meter,weekday=d,event_time=t,command=cmd,notes=(item.get('notes') or '')[:160],is_enabled=True))
    with transaction.atomic():
        MeterTimingEvent.objects.filter(meter=meter).delete(); MeterTimingEvent.objects.bulk_create(rows)
    return len(rows)


@login_required
def meter_schedule_list(request):
    qs=Meter.objects.select_related('unit','unit__property').prefetch_related('timing_events').order_by('unit__property__property_name','unit__unit_number','meter_number')
    meter_q=(request.GET.get('meter') or '').strip(); prop=request.GET.get('property'); unit=request.GET.get('unit'); status=request.GET.get('status'); command=request.GET.get('command'); day=request.GET.get('day')
    if meter_q: qs=qs.filter(meter_number__icontains=meter_q)
    if prop: qs=qs.filter(unit__property_id=prop)
    if unit: qs=qs.filter(unit_id=unit)
    if command in {'on','off'}: qs=qs.filter(timing_events__command=command)
    if day and day.isdigit(): qs=qs.filter(timing_events__weekday=int(day))
    qs=qs.distinct()
    rows=[]
    for i,m in enumerate(qs,1):
        groups=_group_events(m); has=bool(groups)
        if status=='active' and not has: continue
        if status=='none' and has: continue
        nxt=next_schedule_event(m)
        rows.append({'sn':i,'meter':m,'groups':groups,'has_schedule':has,'allowed_now':schedule_allows_power(m),'next':nxt})
    from properties.models import Property, Unit
    return render(request,'smart_meter/meter_schedule_list.html',{'rows':rows,'meters':Meter.objects.order_by('meter_number'),'properties':Property.objects.order_by('property_name'),'units':Unit.objects.order_by('unit_number'),'days':DAYS})

@login_required
@require_POST
def meter_schedule_update(request,meter_id):
    meter=get_object_or_404(Meter,pk=meter_id)
    try: count=_save_json(meter,request.POST.get('events_json'))
    except ValueError as e: messages.error(request,str(e)); return redirect(request.POST.get('next') or 'smart_meter:meter_schedule_list')
    messages.success(request,f'Saved {count} schedule event(s) for meter {meter.meter_number}.')
    return redirect(request.POST.get('next') or 'smart_meter:meter_schedule_list')

@login_required
@require_POST
def meter_schedule_copy(request,meter_id):
    target=get_object_or_404(Meter,pk=meter_id); source=get_object_or_404(Meter,pk=request.POST.get('source_meter_id'))
    if source.pk==target.pk: messages.error(request,'Choose a different source meter.')
    else: messages.success(request,f'Copied {copy_timing_schedule(source,target)} event(s) from {source.meter_number}.')
    return redirect(request.POST.get('next') or 'smart_meter:meter_schedule_list')

@login_required
def meter_schedule_detail(request,meter_id):
    meter=get_object_or_404(Meter.objects.select_related('unit','unit__property').prefetch_related('timing_events'),pk=meter_id)
    events=list(meter.timing_events.all()); nxt=next_schedule_event(meter); allowed=schedule_allows_power(meter)
    summary=[]
    for d,label in DAYS:
        ev=[e for e in events if e.weekday==d]
        summary.append({'label':label,'count':len(ev),'on':sum(e.command=='on' for e in ev),'off':sum(e.command=='off' for e in ev)})
    today=datetime.now().weekday()
    return render(request,'smart_meter/meter_schedule_detail.html',{'meter':meter,'events':events,'groups':_group_events(meter),'next':nxt,'allowed_now':allowed,'summary':summary,'days':DAYS,'today_events':[e for e in events if e.weekday==today],'meters':Meter.objects.exclude(pk=meter.pk).order_by('meter_number')})
