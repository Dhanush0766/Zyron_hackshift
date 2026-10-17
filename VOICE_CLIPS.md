# Voice clips to record

The device plays recorded clips from its SD card: `/voice/<lang>/<KEY>.wav`, in **16-bit PCM mono WAV** (16 kHz is enough). Use a clear, slow, familiar voice. Elderly patients understand a person they know better than a synthetic voice.

- `en` and `hi`: the text is in `backend/medgate/core/prompts.py`. Read it aloud and record it.
- `mni` (Manipuri): **to be written and recorded by a Manipuri speaker on the team.** We did not machine-translate Manipuri, because a wrong medical instruction is worse than none. Until the clips exist, the dashboard shows the English text marked "English fallback".

| KEY | When the device says it | English text |
|---|---|---|
| DUE | Dose time | It is time for your medicine. Please tap your card, place your finger on the sensor and measure your blood pressure. |
| RETAKE | Reading unclear | The reading was not clear. Please sit still and measure again. |
| SCAN_CARD | No card tapped | Please tap your patient card on the reader. |
| WRONG_PATIENT | Someone else's card | This card does not belong to this medicine box. The medicine will not be given. |
| CONFIRM_MEAL | "Take with food" medicine | This medicine must be taken with food. If you have eaten, hold your hand over the tray for two seconds. |
| HELD | Gate stopped the dose | Your readings need a doctor's check. Your doctor and family have been informed. Do not take this dose now. |
| TOO_SOON | Double-dose protection | You already had this medicine recently. It will not be given again now. |
| DELIVERED | Tablet in tray | Your tablet is in the tray. Please take it now. |
| REMINDER | Still not picked up (L2) | Reminder: your tablet is still waiting in the tray. |
| COLLECTED | Pickup confirmed | Thank you. Your dose has been recorded. |
| SOS_ACK | Patient asked for HELP | Help is on the way. Your family and doctor have been alerted. Please sit down and stay calm. |
| EXPIRED | Strip past its expiry date | This medicine has expired and will not be given. Your family has been told to replace it. |

Folder layout on the SD card:
```
/voice/en/DUE.wav ... /voice/en/COLLECTED.wav
/voice/hi/DUE.wav ...
/voice/mni/DUE.wav ...
```
Choose the language per patient in Dashboard → Devices → "Patient, card & voice language". To add Manipuri text to the dashboard, add an `"mni": "..."` entry for each key in `prompts.py`.
