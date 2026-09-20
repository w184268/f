plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.kid.watchbridge"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.kid.watchbridge"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    // 用统一的 compilerOptions 写法：Kotlin 1.9 和 2.x 都认。
    // （老写法 kotlinOptions.jvmTarget 在 Kotlin 2.x 被移除了，IDE 会报错）
    kotlin {
        compilerOptions {
            jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
        }
    }
}

// 零第三方依赖：只用 Android 系统自带 API + HttpURLConnection。
// 这样即使构建机访问不了 Maven（被墙 / 代理故障），也能正常编译出 APK。
dependencies {
}
