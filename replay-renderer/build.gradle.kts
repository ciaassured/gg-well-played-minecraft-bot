plugins {
    id("net.fabricmc.fabric-loom") version "1.17.19"
    java
}

group = property("maven_group") as String
version = property("mod_version") as String

base {
    archivesName = property("archives_base_name") as String
}

repositories {
    mavenCentral()
}

val replayModJar =
    providers
        .gradleProperty("replayModJar")
        .orElse(providers.environmentVariable("REPLAY_MOD_JAR"))

dependencies {
    minecraft("com.mojang:minecraft:${property("minecraft_version")}")
    implementation("net.fabricmc:fabric-loader:${property("loader_version")}")
    implementation("net.fabricmc.fabric-api:fabric-api:${property("fabric_api_version")}")
    compileOnly(files(replayModJar))
}

tasks.withType<JavaCompile>().configureEach {
    options.release = 25
    options.encoding = "UTF-8"
}

tasks.processResources {
    inputs.property("version", project.version)
    filesMatching("fabric.mod.json") {
        expand("version" to project.version)
    }
}

val coreTest by tasks.registering(JavaExec::class) {
    dependsOn(tasks.testClasses)
    classpath = sourceSets.test.get().runtimeClasspath
    mainClass = "gg.wellplayed.jump.renderer.core.CoreTestMain"
    jvmArgs("-ea")
}

tasks.test {
    enabled = false
}

tasks.check {
    dependsOn(coreTest)
}

java {
    sourceCompatibility = JavaVersion.VERSION_25
    targetCompatibility = JavaVersion.VERSION_25
    withSourcesJar()
}
