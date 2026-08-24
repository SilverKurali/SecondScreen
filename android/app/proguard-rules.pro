# PSP Android App ProGuard Rules
# Keep all our code (no reflection used)
-keep class com.psp.app.** { *; }

# Keep JSON serialization
-keep class org.json.** { *; }