import java.security.MessageDigest

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

fun deterministicBuildHash(root: File): String {
    val ignored = setOf(".git", ".gradle", ".idea", "build", "captures")
    val includedRoots = setOf("app", "automation", "buildSrc", "gradle")
    val buildRootFiles = setOf(
        "build.gradle", "build.gradle.kts", "gradle.properties",
        "gradlew", "gradlew.bat", "settings.gradle", "settings.gradle.kts",
    )
    val digest = MessageDigest.getInstance("SHA-256")
    root.walkTopDown()
        .filter { it.isFile }
        .filter { file ->
            val relative = file.relativeTo(root)
            val parts = relative.toPath().map { it.toString() }
            if (parts.any { it in ignored }) return@filter false
            val topDir = parts.firstOrNull() ?: ""
            topDir in includedRoots ||
                relative.invariantSeparatorsPath in buildRootFiles ||
                file.name in buildRootFiles
        }
        .sortedBy { it.relativeTo(root).invariantSeparatorsPath }
        .forEach { file ->
            val relative = file.relativeTo(root).invariantSeparatorsPath.toByteArray(Charsets.UTF_8)
            val lenBytes = ByteArray(4)
            val len = relative.size
            lenBytes[0] = (len shr 24 and 0xFF).toByte()
            lenBytes[1] = (len shr 16 and 0xFF).toByte()
            lenBytes[2] = (len shr 8 and 0xFF).toByte()
            lenBytes[3] = (len and 0xFF).toByte()
            digest.update(lenBytes)
            digest.update(relative)
            file.inputStream().use { input ->
                val buffer = ByteArray(64 * 1024)
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    if (read > 0) digest.update(buffer, 0, read)
                }
            }
        }
    return digest.digest().joinToString("") { "%02x".format(it) }
}

val agentBuildHash = providers.environmentVariable("SIKSIK_AGENT_BUILD_SHA256")
    .orElse(provider { deterministicBuildHash(rootProject.projectDir) })

android {
    namespace = "com.siksik.agent"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.siksik.agent"
        minSdk = 26
        targetSdk = 35
        versionCode = 7
        versionName = "0.7.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        buildConfigField("String", "AGENT_VERSION", "\"$versionName\"")
        buildConfigField("String", "AGENT_BUILD_SHA256", "\"${agentBuildHash.get()}\"")
        buildConfigField("String", "API_VERSION", "\"1.0\"")
        buildConfigField("int", "CAPABILITY_SCHEMA_VERSION", "1")
        buildConfigField("int", "API_PORT", "38471")
        buildConfigField("int", "MAX_PHOTO_ITEMS", "50")
        buildConfigField("int", "MAX_CATALOG_ITEMS", "10000")
        buildConfigField("int", "MAX_THUMBNAIL_BYTES", (512 * 1024).toString())
        buildConfigField("int", "MAX_INVENTORY_PAGE_SIZE", "100")
        buildConfigField("int", "QUICK_INVENTORY_ITEMS_PER_SOURCE", "200")
        buildConfigField("int", "MAX_DOCUMENT_TREE_QUEUE", "2048")
        buildConfigField("int", "MAX_SMS_TEXT_LENGTH", "32768")
        buildConfigField("int", "MAX_CONTACT_TEXT_LENGTH", "8192")
        buildConfigField("int", "MAX_UI_TEXT_LENGTH", "512")
        buildConfigField("int", "MAX_UI_NODES", "256")
        buildConfigField("int", "MAX_UI_DEPTH", "16")
        buildConfigField("int", "MAX_CAPTURE_RECORDS", "5000")
        buildConfigField("long", "MAX_PREPROCESS_INPUT_BYTES", "67108864L")
        buildConfigField("long", "MAX_DOCUMENT_INPUT_BYTES", "33554432L")
        buildConfigField("int", "MAX_DOCUMENT_TEXT_CHARS", "65536")
        buildConfigField("int", "MAX_DOCUMENT_ARCHIVE_ENTRIES", "1024")
        buildConfigField("long", "MAX_DOCUMENT_ARCHIVE_BYTES", "134217728L")
        buildConfigField("int", "MAX_PDF_PAGES", "24")
        buildConfigField("long", "MAX_SHARED_VISUAL_PIXELS", "4000000L")
        buildConfigField("long", "MAX_OCR_IMAGE_PIXELS", "4000000L")
        buildConfigField("long", "MAX_VISION_IMAGE_PIXELS", "4000000L")
        buildConfigField("long", "MAX_PERCEPTUAL_HASH_PIXELS", "262144L")
        buildConfigField("int", "MAX_OCR_REGIONS", "128")
        buildConfigField("int", "MAX_OCR_TEXT_CHARS", "32768")
        buildConfigField("int", "MAX_OBJECT_LABELS", "12")
        buildConfigField("int", "MAX_FACE_SIGNALS", "8")
        buildConfigField("int", "MAX_PREPROCESS_CONCURRENCY", "3")
        buildConfigField("long", "PREPROCESS_ITEM_TIMEOUT_MS", "20000L")
        buildConfigField("long", "PREPROCESS_DOCUMENT_TIMEOUT_MS", "120000L")
        buildConfigField("long", "PREPROCESS_SESSION_DEADLINE_MS", "600000L")
        buildConfigField("long", "PREPROCESS_FULL_SESSION_DEADLINE_MS", "21600000L")
        buildConfigField("int", "MAX_STAGE_ITEMS", "10000")
        buildConfigField("long", "MAX_STAGE_FILE_BYTES", "4294967296L")
        buildConfigField("long", "MAX_STAGE_TOTAL_BYTES", "17179869184L")
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
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
        unitTests.isIncludeAndroidResources = true
    }

    lint {
        abortOnError = true
        checkReleaseBuilds = true
    }
}

dependencies {
    implementation("androidx.activity:activity-ktx:1.10.1")
    implementation("androidx.core:core-ktx:1.16.0")
    implementation("androidx.exifinterface:exifinterface:1.4.2")
    implementation("com.google.mediapipe:tasks-vision:0.10.35")
    implementation("com.google.mlkit:text-recognition:16.0.1")
    implementation("org.nanohttpd:nanohttpd:2.3.1")
    implementation("org.apache.poi:poi:5.5.1")
    implementation("org.apache.poi:poi-scratchpad:5.5.1")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
    androidTestImplementation("androidx.test:core-ktx:1.6.1")
    androidTestImplementation("androidx.test.ext:junit-ktx:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
}

tasks.register("printBuildHash") {
    doLast { println(agentBuildHash.get()) }
}
