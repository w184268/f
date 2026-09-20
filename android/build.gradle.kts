// 顶层构建文件。
//
// 版本这里统一定义，app/build.gradle.kts 引用，避免两处对不上。
//
// 关于版本选择：AGP 8.5.2 + Kotlin 1.9.24 是一组经过大量验证的搭配，
// 只要求 JDK 17，兼容性最好。如果你的 Android Studio 自带更高版本的
// Kotlin 插件并提示不匹配，把下面的 kotlin 版本改成 Studio 建议的那个即可。
plugins {
    id("com.android.application") version "8.5.2" apply false
    id("org.jetbrains.kotlin.android") version "1.9.24" apply false
}
