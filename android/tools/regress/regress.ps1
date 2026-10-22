# On-phone regression suite for MBUDDY. Run from this folder with the phone connected (USB debugging) and a debug build installed:
#   powershell -File regress.ps1              all cases
#   powershell -File regress.ps1 -Only parse  one group
# Test mode: every confirmation is answered "no", so nothing is sent or called. It leaves some test questions in the Buddy conversation.
# On-phone regression checks, run before every install is handed over. Test mode: every confirmation is answered "no", nothing is sent or called.
param([string]$Only = "")
$adb = if ($env:ANDROID_HOME) { Join-Path $env:ANDROID_HOME "platform-tools\adb.exe" } else { "adb" }
$s = $PSScriptRoot
# the app's screen must be open (after an install only its listening service may be running, with no page to talk to)
& $adb shell monkey -p app.mbuddy.mobile -c android.intent.category.LAUNCHER 1 2>$null | Out-Null; Start-Sleep 6
$p = (& $adb shell pidof app.mbuddy.mobile).Trim()
& $adb forward tcp:9222 localabstract:webview_devtools_remote_$p | Out-Null
function js($code) { Set-Content "$s\rg.js" $code -Encoding utf8; node "$s\cdp.mjs" "@$s\rg.js" }
js "(window.Native.setVoice(false), 'voice off')" | Out-Null
$fail = 0; $pass = 0

# 1. how sentences are read as messages (no app is opened)
$parse = @(
  @{ q = "send hi to Baba on WhatsApp"; want = "to='Baba' text='hi' app=whatsapp" },
  @{ q = "In WhatsApp and send Baba only hi"; want = "to='Baba' text='hi' app=whatsapp" },
  @{ q = "Send Baba a text message hello on SMS"; want = "to='Baba' text='hello' app=sms" },
  @{ q = "open WhatsApp send Baba hi how are you"; want = "to='Baba' text='hi how are you' app=whatsapp" },
  @{ q = "Close YouTube and open WhatsApp send Baba hi"; want = "to='Baba' text='hi' app=whatsapp" }
)
foreach ($c in $parse) {
  if ($Only -and "parse" -notlike $Only) { break }
  & $adb logcat -c
  js "(window.Native.debugParse($(ConvertTo-Json $c.q -Compress)), 'ok')" | Out-Null
  Start-Sleep 1
  $got = ((& $adb logcat -d -s MBuddyDebug:*) | Where-Object { $_ -match "parse '" } | Select-Object -Last 1) -replace ".*-> ", ""
  if ($got -eq $c.want) { $pass++; "PASS parse  $($c.q)" } else { $fail++; "FAIL parse  $($c.q)`n     want: $($c.want)`n     got:  $got" }
}

# 2. whole requests through the brain, checked at the result: which app the agent worked in, what it would have asked, what was said
$cases = @(
  @{ id = "wa-send"; q = "Send hi to Baba on WhatsApp"; app = "com.whatsapp"; ask = "^To Baba\. I wrote: Hi\. Shall I send it\?$"; secs = 60 },
  @{ id = "sms-send"; q = "Send Baba a text message hello on SMS"; app = "com.google.android.apps.messaging"; ask = "^To Baba\. (I wrote|The message): Hello\. Shall I send it\?$"; secs = 60 },
  @{ id = "call"; q = "Call Baba"; ask = "^Call Baba now\?$"; secs = 60 },
  @{ id = "denied"; q = "Open the Whish Money app and show me my balance"; finish = "fail: I don't operate"; maxSteps = 0; secs = 40 },
  @{ id = "app-task"; q = "Open YouTube and search for Mr Beast then pick the second video"; say = "On it"; first = "YouTube on the search"; front = "com.google.android.youtube"; secs = 25 },
  @{ id = "open"; q = "open Spotify"; say = "Opening Spotify"; front = "com.spotify.music"; secs = 15 },
  @{ id = "web"; q = "how tall is Mount Everest"; web = "8,?8[0-9][0-9]|29,?0[0-9][0-9]"; secs = 60 },
  @{ id = "web-capital"; q = "what's the capital of Lebanon"; web = "Beirut"; secs = 60 },
  @{ id = "web-who"; q = "who is the president of France"; web = "Macron"; secs = 60 }
)
foreach ($c in $cases) {
  if ($Only -and $c.id -notlike $Only) { continue }
  & $adb shell input keyevent KEYCODE_HOME; Start-Sleep 2
  & $adb logcat -c
  js "(window.Native.debugRespond($(ConvertTo-Json $c.q -Compress)), 'sent')" | Out-Null
  $deadline = (Get-Date).AddSeconds($c.secs); $log = ""
  while ((Get-Date) -lt $deadline) {
    Start-Sleep 3
    $log = (& $adb logcat -d -v time -s MBuddyAgent:* MBuddyTest:* MBuddyDebug:* MBuddyBrain:* MBuddyWeb:*) -join "`n"
    $agentDone = $log -match "finish (done|fail)"; $said = $log -match "MBuddyDebug"
    if ($said -and ($agentDone -or ($log -notmatch "MBuddyAgent") -or $c.id -eq "app-task")) { Start-Sleep 2; $log = (& $adb logcat -d -v time -s MBuddyAgent:* MBuddyTest:* MBuddyDebug:* MBuddyBrain:* MBuddyWeb:*) -join "`n"; break }
  }
  $why = @()
  $debug = ([regex]::Matches($log, 'MBuddyDebug\(\s*\d+\): (.*)') | Select-Object -Last 1).Groups[1].Value
  $asks = [regex]::Matches($log, 'WOULD ASK: (.*)') | ForEach-Object { $_.Groups[1].Value.Trim() }
  $apps = [regex]::Matches($log, 'step \d+ app=(\S+)') | ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique
  $steps = ([regex]::Matches($log, 'step \d+ app=')).Count
  if ($c.app -and ($apps -notcontains $c.app -or ($apps | Where-Object { $_ -ne $c.app }).Count -gt 0)) { $why += "worked in [$($apps -join ',')], want $($c.app)" }
  if ($c.ask -and -not ($asks | Where-Object { $_ -match $c.ask })) { $why += "asked [$($asks -join ' | ')]" }
  if ($c.finish -and $log -notmatch [regex]::Escape("finish $($c.finish)")) { $why += "finish: " + (([regex]::Matches($log, 'finish (.*)') | Select-Object -Last 1).Groups[1].Value) }
  if ($c.ContainsKey("maxSteps") -and $steps -gt $c.maxSteps) { $why += "$steps steps" }
  if ($c.say -and $debug -notmatch [regex]::Escape($c.say)) { $why += "said: $debug" }
  if ($c.first -and $log -notmatch [regex]::Escape($c.first)) { $why += "first move missing" }
  if ($c.web -and $debug -notmatch $c.web) { $why += "web said: $debug" }
  if ($c.front) { $top = (& $adb shell dumpsys activity activities | Select-String "topResumedActivity" | Select-Object -First 1).Line; if ($top -notmatch [regex]::Escape($c.front)) { $why += "in front: " + ($top -replace '.*u0 ', '' -replace ' t\d+\}', '') } }
  if ($why.Count -eq 0) { $pass++; "PASS $($c.id)  $($c.q)" } else { $fail++; "FAIL $($c.id)  $($c.q)`n     " + ($why -join "`n     ") }
  # stop anything still running before the next case
  js "(window.Native.agentStop && window.Native.agentStop(), 'stopped')" | Out-Null; Start-Sleep 3
}
& $adb shell input keyevent KEYCODE_HOME
js "(window.Native.setVoice(true), 'voice on')" | Out-Null
"REGRESSION: $pass passed, $fail failed"
