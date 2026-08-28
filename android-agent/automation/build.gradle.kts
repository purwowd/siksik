plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.siksik.agent"
    compileSdk = 35

    defaultConfig {
        minSdk = 26
        targetSdk = 35
        applicationId = "com.siksik.agent.automation"
        versionCode = 1
        versionName = "1.0"
        testInstrumentationRunner = "com.siksik.agent.automation.SiksikAndroidJUnitRunner"

        buildConfigField("int", "MAX_SMS_TEXT_LENGTH", "32768")
        buildConfigField("int", "MAX_CONTACT_TEXT_LENGTH", "8192")
        buildConfigField("int", "MAX_UI_TEXT_LENGTH", "512")
        buildConfigField("int", "MAX_UI_NODES", "256")
        buildConfigField("int", "MAX_UI_DEPTH", "16")
        buildConfigField("int", "MAX_CAPTURE_RECORDS", "5000")
    }

    sourceSets {
        getByName("main") {
            java.setSrcDirs(
                listOf(
                    "src/main/java",
                    "../app/src/main/java/com/siksik/agent/source/communication/shared",
                ),
            )
        }
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }

    lint {
        abortOnError = true
    }
}

dependencies {
    implementation("androidx.test:core-ktx:1.6.1")
    implementation("androidx.test.ext:junit-ktx:1.2.1")
    implementation("androidx.test:runner:1.6.2")
    implementation("androidx.test.uiautomator:uiautomator:2.3.0")
    implementation("com.google.mlkit:text-recognition:16.0.1")
    testImplementation("junit:junit:4.13.2")
}
