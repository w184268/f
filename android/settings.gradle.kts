pluginManagement {
    repositories {
        // 国内直连 Google / Maven 中央经常超时，这里把阿里云镜像排在前面。
        // 镜像同步的是同一批构件，内容一致；连不上时 Gradle 会自动回落到官方源。
        maven {
            url = uri("https://maven.aliyun.com/repository/google")
            content { includeGroupByRegex("com\\.android.*"); includeGroupByRegex("com\\.google.*"); includeGroupByRegex("androidx.*") }
        }
        maven { url = uri("https://maven.aliyun.com/repository/gradle-plugin") }
        maven { url = uri("https://maven.aliyun.com/repository/public") }
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        maven { url = uri("https://maven.aliyun.com/repository/google") }
        maven { url = uri("https://maven.aliyun.com/repository/public") }
        google()
        mavenCentral()
    }
}
rootProject.name = "WatchBridge"
include(":app")
