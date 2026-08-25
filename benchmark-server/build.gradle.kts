plugins {
    java
}

group = "gg.wellplayed.jump"
version = "1.0.0"

repositories {
    mavenCentral()
    maven("https://repo.papermc.io/repository/maven-public/")
}

dependencies {
    compileOnly("io.papermc.paper:paper-api:26.2.build.112-stable")
    implementation("com.google.protobuf:protobuf-java:4.35.1")
}

java {
    toolchain.languageVersion = JavaLanguageVersion.of(25)
}

val protocolDir = providers.gradleProperty("protocolDir").orElse("../protocol")
val generatedProtocol = layout.buildDirectory.dir("generated/source/proto/main/java")

sourceSets.main {
    java.srcDir(generatedProtocol)
}

val generateProto by tasks.registering(Exec::class) {
    val schema = protocolDir.map { file("$it/proto/jump/v1/jump.proto") }
    inputs.file(schema)
    outputs.dir(generatedProtocol)
    doFirst {
        val protocolRoot = file(protocolDir.get()).absoluteFile
        delete(generatedProtocol)
        generatedProtocol.get().asFile.mkdirs()
        commandLine(
            "protoc",
            "--proto_path=${protocolRoot.resolve("proto")}",
            "--java_out=${generatedProtocol.get().asFile}",
            schema.get().absolutePath,
        )
    }
}

tasks.compileJava {
    dependsOn(generateProto)
    options.encoding = "UTF-8"
    options.release = 25
}

val pluginJar by tasks.registering(Jar::class) {
    dependsOn(tasks.classes)
    archiveClassifier = "all"
    duplicatesStrategy = DuplicatesStrategy.EXCLUDE
    from(sourceSets.main.get().output)
    from({
        configurations.runtimeClasspath.get().map {
            if (it.isDirectory) it else zipTree(it)
        }
    })
    exclude("META-INF/*.SF", "META-INF/*.RSA", "META-INF/*.DSA")
}

val coreTest by tasks.registering(JavaExec::class) {
    dependsOn(tasks.testClasses)
    classpath = sourceSets.test.get().runtimeClasspath
    mainClass = "gg.wellplayed.jump.server.core.CoreTestMain"
    jvmArgs("-ea")
}

tasks.test {
    enabled = false
}

tasks.check {
    dependsOn(coreTest)
}

tasks.build {
    dependsOn(pluginJar)
}
