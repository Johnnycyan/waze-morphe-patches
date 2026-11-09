group = "app.waze.elevenlabs"

// The Waze version this bundle targets lives in upstream.lock (WAZE_VERSION), which
// scripts/sync.sh keeps equal to the newest version nl-morphe-patches targets.
val wazeVersion = providers.fileContents(rootProject.layout.projectDirectory.file("upstream.lock")).asText.map { lock ->
    Regex("""(?m)^WAZE_VERSION=([0-9.]+)\s*$""").find(lock)?.groupValues?.get(1)
        ?: error("upstream.lock has no WAZE_VERSION")
}
val generatedTarget = layout.buildDirectory.dir("generated/waze-target/kotlin")
kotlin.sourceSets.main { kotlin.srcDir(generatedTarget) }
val generateWazeTarget = tasks.register("generateWazeTarget") {
    inputs.property("wazeVersion", wazeVersion)
    outputs.dir(generatedTarget)
    doLast {
        val file = generatedTarget.get().asFile.resolve("app/waze/elevenlabs/WazeTarget.kt")
        file.parentFile.mkdirs()
        file.writeText("package app.waze.elevenlabs\n\ninternal const val WAZE_VERSION = \"${wazeVersion.get()}\"\n")
    }
}
tasks.named("compileKotlin") { dependsOn(generateWazeTarget) }
tasks.named("sourcesJar") { dependsOn(generateWazeTarget) }

patches {
    about {
        name = "Waze ElevenLabs Voice"
        description = "Speaks Waze navigation through ElevenLabs voices, configured from Waze's own settings"
        source = "https://github.com/Johnnycyan/waze-morphe-patches"
        author = "Johnnycyan"
        contact = "https://github.com/Johnnycyan/waze-morphe-patches/issues"
        website = "https://github.com/Johnnycyan/waze-morphe-patches"
        license = "GPLv3"
    }
}

// Separate configuration so gson is available to generatePatchesList but never bundled.
val patchListGeneratorClasspath = configurations.create("patchListGeneratorClasspath")

dependencies {
    compileOnly(libs.gson)
    patchListGeneratorClasspath(libs.gson)
}

tasks {
    register<JavaExec>("generatePatchesList") {
        description = "Writes patches-list.json for the built bundle"
        dependsOn(build)
        classpath = sourceSets["main"].runtimeClasspath + patchListGeneratorClasspath
        mainClass.set("util.PatchListGeneratorKt")
    }
}
