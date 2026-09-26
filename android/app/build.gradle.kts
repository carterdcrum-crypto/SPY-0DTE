plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

val apiBaseUrl = providers.gradleProperty("SPY_API_BASE_URL")
    .orElse("https://spy-0dte-collector-production.up.railway.app")
    .get()
val googleWebClientId = providers.gradleProperty("GOOGLE_WEB_CLIENT_ID").orElse("").get()

android {
    namespace = "app.spy0dte.mobile"
    compileSdk = 36

    defaultConfig {
        applicationId = "app.spy0dte.mobile"
        minSdk = 28
        targetSdk = 36
        versionCode = 5
        versionName = "0.1.4"

        buildConfigField("String", "API_BASE_URL", "\"${apiBaseUrl}\"")
        buildConfigField("String", "GOOGLE_WEB_CLIENT_ID", "\"${googleWebClientId}\"")
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    // Compose 1.11.x is the newest stable line that remains compatible with API 36.
    val composeBom = platform("androidx.compose:compose-bom:2026.04.01")
    implementation(composeBom)

    implementation("androidx.activity:activity-compose:1.11.0")
    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.10.0")

    implementation("androidx.credentials:credentials:1.6.0")
    implementation("androidx.credentials:credentials-play-services-auth:1.6.0")
    implementation("com.google.android.libraries.identity.googleid:googleid:1.1.1")

    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    debugImplementation("androidx.compose.ui:ui-tooling")
}
