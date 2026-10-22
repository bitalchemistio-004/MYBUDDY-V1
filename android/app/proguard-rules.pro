# Called from the web pages by name: the bridge methods must keep their names.
-keepclassmembers class app.mbuddy.mobile.Bridge { @android.webkit.JavascriptInterface <methods>; }
-keepclassmembers class app.mbuddy.mobile.Overlay$Ui { @android.webkit.JavascriptInterface <methods>; }
# Android creates these from the manifest.
-keep class app.mbuddy.mobile.** extends android.app.Service
-keep class app.mbuddy.mobile.** extends android.content.BroadcastReceiver
-keep class app.mbuddy.mobile.** extends android.accessibilityservice.AccessibilityService
-keep class app.mbuddy.mobile.** extends android.service.voice.VoiceInteractionService
-keep class app.mbuddy.mobile.** extends android.service.voice.VoiceInteractionSessionService
-keep class app.mbuddy.mobile.** extends android.speech.RecognitionService
-keep class app.mbuddy.mobile.** extends android.service.notification.NotificationListenerService
-keep class app.mbuddy.mobile.** extends android.app.Activity
# The on-device model and speech runtimes use native code that finds classes by name.
-keep class com.google.ai.edge.litertlm.** { *; }
-keep class org.vosk.** { *; }
-keep class com.sun.jna.** { *; }
-dontwarn javax.annotation.**
-dontwarn org.slf4j.**

# JNA names desktop classes it never uses on Android.
-dontwarn java.awt.**
-dontwarn javax.swing.**
-dontwarn java.beans.**
