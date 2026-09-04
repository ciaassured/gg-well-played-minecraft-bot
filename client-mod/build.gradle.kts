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

val protobufDependency = "com.google.protobuf:protobuf-java:${property("protobuf_version")}"

dependencies {
    minecraft("com.mojang:minecraft:${property("minecraft_version")}")
    implementation("net.fabricmc:fabric-loader:${property("loader_version")}")
    implementation("net.fabricmc.fabric-api:fabric-api:${property("fabric_api_version")}")
    implementation(protobufDependency)
    include(protobufDependency)
}

val protocolDir = providers.gradleProperty("protocolDir").orElse("../protocol")
val protocolRoot = protocolDir.map { file(it) }
val generatedProtoDir = layout.buildDirectory.dir("generated/source/proto/main/java")

val generateProto by tasks.registering(Exec::class) {
    val source = protocolRoot.map { it.resolve("proto/yrush/v1/yrush.proto") }
    inputs.file(source)
    outputs.dir(generatedProtoDir)
    doFirst {
        delete(generatedProtoDir)
        mkdir(generatedProtoDir)
    }
    commandLine(
        "protoc",
        protocolRoot.map { "--proto_path=${it.resolve("proto").absolutePath}" }.get(),
        generatedProtoDir.map { "--java_out=${it.asFile}" }.get(),
        source.get().absolutePath,
    )
}

sourceSets.main {
    java.srcDir(generatedProtoDir)
}

tasks.withType<JavaCompile>().configureEach {
    dependsOn(generateProto)
    options.release = 25
    options.encoding = "UTF-8"
}

tasks.withType<Jar>().configureEach {
    if (name == "sourcesJar") {
        dependsOn(generateProto)
    }
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
    mainClass = "gg.wellplayed.yrush.client.core.CoreTestMain"
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
