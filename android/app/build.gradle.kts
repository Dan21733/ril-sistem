plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Nomor versi dari GitHub Actions (run_number), supaya setiap build bisa dipasang di atas yang lama.
val nomorVersi = (System.getenv("RIL_VERSION_CODE") ?: "1").toInt()
val kunciRilis: String? = System.getenv("RIL_KEYSTORE")

android {
    namespace = "id.dbrl.ril"
    compileSdk = 34

    defaultConfig {
        applicationId = "id.dbrl.ril"
        minSdk = 26
        targetSdk = 34
        versionCode = nomorVersi
        versionName = "0.1.$nomorVersi"
    }

    signingConfigs {
        create("rilis") {
            if (kunciRilis != null) {
                storeFile = file(kunciRilis)
                storePassword = System.getenv("RIL_KEYSTORE_PASSWORD")
                keyAlias = "ril"
                keyPassword = System.getenv("RIL_KEYSTORE_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // Selalu kunci yang sama: aplikasi device owner hanya bisa diperbarui dengan tanda tangan yang sama.
            signingConfig = if (kunciRilis != null) signingConfigs.getByName("rilis") else signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}
