use std::env;
use std::time::{SystemTime, UNIX_EPOCH};

fn value_or_env(key: &str, default: &str) -> String {
    env::var(key)
        .ok()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| default.to_string())
}

fn unix_build_time_fallback() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    format!("unix-{secs}")
}

fn main() {
    let build_git_sha = value_or_env("BUILD_GIT_SHA", "unknown");
    let build_git_dirty = value_or_env("BUILD_GIT_DIRTY", "unknown");
    let build_source = value_or_env("BUILD_SOURCE", "unknown");
    let build_image_id = value_or_env("BUILD_IMAGE_ID", "unknown");
    let build_time_utc = value_or_env("BUILD_TIME_UTC", &unix_build_time_fallback());

    println!("cargo:rustc-env=BUILD_GIT_SHA={build_git_sha}");
    println!("cargo:rustc-env=BUILD_GIT_DIRTY={build_git_dirty}");
    println!("cargo:rustc-env=BUILD_SOURCE={build_source}");
    println!("cargo:rustc-env=BUILD_IMAGE_ID={build_image_id}");
    println!("cargo:rustc-env=BUILD_TIME_UTC={build_time_utc}");

    println!("cargo:rerun-if-env-changed=BUILD_GIT_SHA");
    println!("cargo:rerun-if-env-changed=BUILD_GIT_DIRTY");
    println!("cargo:rerun-if-env-changed=BUILD_SOURCE");
    println!("cargo:rerun-if-env-changed=BUILD_IMAGE_ID");
    println!("cargo:rerun-if-env-changed=BUILD_TIME_UTC");
}
