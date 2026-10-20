/**
 * Utility functions for CDK deployment
 */

export function getForceRedeployTimestamp(): string {
  return process.env.CDK_SYNTH_MODE ? "NONE" : new Date().toISOString();
}

export function getDockerImageTag(): string {
  return process.env.CDK_SYNTH_MODE
    ? "v1"
    : new Date()
        .toISOString()
        .replace(/[^0-9]/g, "")
        .slice(0, 14);
}

export function getBuildTimestamp(): string {
  return process.env.CDK_SYNTH_MODE ? "NONE" : new Date().toISOString();
}
