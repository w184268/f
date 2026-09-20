# WatchBridge 混淆规则
# release 构建时才生效；本项目没有反射调用，保持默认即可。
-keep class com.kid.watchbridge.** { *; }
-dontwarn **
