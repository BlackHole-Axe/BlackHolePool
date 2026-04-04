use std::env;

use serde::Serialize;

const BINARY_NAME: &str = env!("CARGO_PKG_NAME");
const VERSION: &str = env!("CARGO_PKG_VERSION");

fn build_git_sha() -> &'static str {
    option_env!("BUILD_GIT_SHA").unwrap_or("unknown")
}

fn build_git_dirty() -> &'static str {
    option_env!("BUILD_GIT_DIRTY").unwrap_or("unknown")
}

fn build_source() -> &'static str {
    option_env!("BUILD_SOURCE").unwrap_or("unknown")
}

fn build_image_id() -> &'static str {
    option_env!("BUILD_IMAGE_ID").unwrap_or("unknown")
}

fn build_time_utc() -> &'static str {
    option_env!("BUILD_TIME_UTC").unwrap_or("unknown")
}

#[derive(Debug, Clone, Serialize)]
pub struct BuildInfo {
    pub binary_name: &'static str,
    pub version: &'static str,
    pub git_sha: &'static str,
    pub git_dirty: &'static str,
    pub build_time_utc: &'static str,
    pub build_source: &'static str,
    pub build_image_id: String,
    pub runtime_image_ref: String,
    pub runtime_image_id: String,
    pub runtime_container_name: String,
}

fn runtime_env(key: &str, default: &str) -> String {
    env::var(key)
        .ok()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| default.to_string())
}

pub fn current() -> BuildInfo {
    let runtime_image_id = runtime_env("RUNTIME_IMAGE_ID", build_image_id());
    let build_image_id = if build_image_id() == "unknown" {
        runtime_image_id.clone()
    } else {
        build_image_id().to_string()
    };

    BuildInfo {
        binary_name: BINARY_NAME,
        version: VERSION,
        git_sha: build_git_sha(),
        git_dirty: build_git_dirty(),
        build_time_utc: build_time_utc(),
        build_source: build_source(),
        build_image_id,
        runtime_image_ref: runtime_env("RUNTIME_IMAGE_REF", "unknown"),
        runtime_image_id,
        runtime_container_name: runtime_env("RUNTIME_CONTAINER_NAME", "unknown"),
    }
}

pub fn one_line() -> String {
    let info = current();
    format!(
        "binary={} version={} git_sha={} dirty={} build_time_utc={} build_source={} build_image_id={} runtime_image_ref={} runtime_image_id={} runtime_container_name={}",
        info.binary_name,
        info.version,
        info.git_sha,
        info.git_dirty,
        info.build_time_utc,
        info.build_source,
        info.build_image_id,
        info.runtime_image_ref,
        info.runtime_image_id,
        info.runtime_container_name
    )
}
