# Workout on the watch: separate verification

Creating and scheduling a workout in Garmin Connect is not proof that it reached your watch. This project records the watch step separately, as your own observation.

## Send and find the workout

1. Open **Garmin Connect app → Training & Planning → Workouts** and find the workout. Every workout this project creates starts with a unique marker such as **`[ARC-0123456789abcdef]`**, followed by its descriptive title.
2. Select **Send to Device** and choose your watch. Keep the app open and the paired watch nearby, then sync the watch (on many models: hold **LIGHT → Sync**).
3. On the watch, open the run activity's **Training → Workouts** list. Because of the marker, a shortened watch label may show only an `[ARC-…` prefix; Garmin's [workout support article](https://support.garmin.com/en-CA/?faq=wZ52AaLbLG2GC1Lxu2l4k7) notes that some devices show only the first 15 characters.
4. Open the workout and scroll through its warmup, run and cooldown steps without starting the timer. Check that the steps and date match the dashboard.

The watch calendar can require a separate send of the Connect training calendar. If a workout appears in **Workouts** but not on the calendar glance, record those as separate outcomes. Menus differ between models; consult your watch's manual.

Garmin's [send-workout instructions](https://www8.garmin.com/manuals/webhelp/GUID-F41EAFB3-6CC9-42DE-9C6C-9E358DBB0671/EN-US/GUID-6E339F62-28D2-4C2A-8534-F90D8C27D6E2.html), [manual sync instructions](https://www8.garmin.com/manuals/webhelp/GUID-F41EAFB3-6CC9-42DE-9C6C-9E358DBB0671/EN-US/GUID-0DC94101-1486-420B-8EA5-EFF3B32C3B0D.html), [starting-a-workout instructions](https://www8.garmin.com/manuals/webhelp/GUID-F41EAFB3-6CC9-42DE-9C6C-9E358DBB0671/EN-US/GUID-71017882-35BA-457C-9F94-E4BE2C882E48.html) and [training-calendar instructions](https://www8.garmin.com/manuals/webhelp/GUID-F41EAFB3-6CC9-42DE-9C6C-9E358DBB0671/EN-US/GUID-F5EB9C7C-A74E-4A26-BFB5-1E6DA4399067.html) document the separate transfer, workout-list and calendar steps.

## Record what you saw

Only after seeing the workout on the actual device:

```bash
python3 -m coach workout watch-verified SESSION_ID --note "Watch model, workout name, date, and what I saw"
```

The dashboard's **I see it on my watch** button does the same. The registry saves the timestamp and note as `user_statement` device evidence. Creation, calendar verification and watch verification remain separate states. If the workout is missing, leave watch verification pending and check device selection, compatibility and sync. Do not republish the workout just to retry the watch step.
