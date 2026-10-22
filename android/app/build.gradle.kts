import org.gradle.api.tasks.Copy
import java.util.Properties

// Every build is newer than the one before it: the number of commits on this branch (1 when git is not there, as in a source zip).
val commitCount: Int = runCatching {
    val p = ProcessBuilder("git", "rev-list", "--count", "HEAD").directory(rootProject.projectDir).redirectErrorStream(true).start()
    p.inputStream.bufferedReader().readText().trim().toInt().also { p.waitFor() }
}.getOrDefault(1).coerceAtLeast(1)

// The release key: android/keystore.properties (never committed) names it. Without that file a release falls back to the debug key,
// which is fine for installing on your own phone and not for publishing. See docs/BUILD.md.
val keystoreProps = Properties().also { pr -> rootProject.file("keystore.properties").takeIf { it.exists() }?.inputStream()?.use { pr.load(it) } }

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "app.mbuddy.mobile"
    compileSdk = 35

    defaultConfig {
        applicationId = "app.mbuddy.mobile"
        minSdk = 26            // Android 8: notification channels, WorkManager, encrypted preferences
        targetSdk = 35
        versionCode = commitCount
        versionName = "0.1.$commitCount"
        // The phones this runs on are arm64: the other CPU libraries (the speech and model runtimes are large) are not shipped.
        ndk { abiFilters += "arm64-v8a" }
    }

    signingConfigs {
        if (keystoreProps.containsKey("storeFile")) {
            create("release") {
                storeFile = rootProject.file(keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true            // R8 shrinks and obfuscates the code; the rules are in proguard-rules.pro
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")        // the release key when keystore.properties names one, else the debug key (installable here, not publishable)
        }
    }
    lint { checkReleaseBuilds = false }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin { jvmToolchain(17) }

// The phone tools page is the same web page the PC serves (shell/ui). It is copied into the app's assets at build time,
// so the app opens it with no PC and no connection at all.
val copyWeb by tasks.registering(Copy::class) {
    val ui = rootProject.projectDir.parentFile.resolve("shell/ui")
    from(ui) {
        // The phone app (phoneapp.html and js/papp), the card shown over other apps (agent.html), the shared look and buddy art.
        include("phoneapp.html", "agent.html", "phone.html", "css/*.css", "js/api.js", "js/phone-actions.js", "js/phone-page.js", "js/native-shim.js",
                "js/papp/**", "js/buddy/**", "assets/fonts/**", "assets/pwa/icon-192.png", "assets/buddy/chars/**")
    }
    into(layout.buildDirectory.dir("webassets/web"))
}
android.sourceSets["main"].assets.srcDir(layout.buildDirectory.dir("webassets"))
tasks.matching { it.name.startsWith("merge") && it.name.endsWith("Assets") }.configureEach { dependsOn(copyWeb) }

dependencies {
    implementation("androidx.core:core-ktx:1.19.1")
    implementation("androidx.appcompat:appcompat:1.8.0")
    implementation("androidx.webkit:webkit:1.12.1")                       // WebViewAssetLoader: the bundled page gets a real https origin
    implementation("androidx.work:work-runtime-ktx:2.12.0")              // the background check for what MBUDDY put on the phone
    implementation("androidx.security:security-crypto:1.1.0-alpha06")    // the pairing token, encrypted on the phone
    implementation("com.google.ai.edge.litertlm:litertlm-android:0.18.0") // Google's on-device runtime: the model that runs on the phone itself
    implementation("com.alphacephei:vosk-android:0.3.75")                // offline speech recognition: it listens for "hey buddy" with no internet
    testImplementation("junit:junit:4.13.2")                              // the local unit tests (app/src/test): the rules that must not regress
    implementation("net.java.dev.jna:jna:5.19.1@aar")                    // what Vosk's native part needs
}
