export interface CognitoConfig {
  userPoolName: string;
}

export interface S3Config {
  bucketNameAgentPrefix: string;
  removalPolicy: "DESTROY" | "RETAIN";
}

export interface DeploymentConfig {
  stackName: string;
  projectName: string;
  parameterStorePrefix: string;
  tags: Record<string, string>;
  cognito: CognitoConfig;
  s3: S3Config;
}

function getDefaultConfig(): DeploymentConfig {
  return {
    stackName: "AgentCoreStrandsStack",
    projectName: "agentcore-strands",
    parameterStorePrefix: "/agentcore/",
    tags: {},
    cognito: {
      userPoolName: "agentcore-user-pool",
    },
    s3: {
      bucketNameAgentPrefix: "agentcore-files",
      removalPolicy: "DESTROY",
    },
  };
}

/**
 * Environment-specific overrides. Each key is an environment name
 * and the value is a partial DeploymentConfig that gets deep-merged
 * on top of the defaults.
 */
const ENV_OVERRIDES: Record<string, DeepPartial<DeploymentConfig>> = {
  dev: {
    stackName: "AgentCoreStrandsDev",
    tags: { Environment: "dev" },
    s3: { removalPolicy: "DESTROY" },
  },
  staging: {
    stackName: "AgentCoreStrandsStaging",
    tags: { Environment: "staging" },
  },
  prod: {
    stackName: "AgentCoreStrandsProd",
    tags: { Environment: "prod" },
  },
};

/** Recursive partial type for deep merge support. */
type DeepPartial<T> = {
  [P in keyof T]?: T[P] extends object ? DeepPartial<T[P]> : T[P];
};

/** Deep merge source into target, returning a new object. Arrays are replaced, not merged. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function deepMerge(target: any, source: any): any {
  const result = { ...target };
  for (const key of Object.keys(source)) {
    const sourceVal = source[key];
    const targetVal = target[key];
    if (
      sourceVal !== undefined &&
      typeof sourceVal === "object" &&
      sourceVal !== null &&
      !Array.isArray(sourceVal) &&
      typeof targetVal === "object" &&
      targetVal !== null &&
      !Array.isArray(targetVal)
    ) {
      result[key] = deepMerge(targetVal, sourceVal);
    } else if (sourceVal !== undefined) {
      result[key] = sourceVal;
    }
  }
  return result;
}

/**
 * Returns a fully-populated DeploymentConfig.
 * When called with no arguments, returns defaults.
 * When called with an environment name, merges that environment's
 * overrides on top of the defaults.
 */
export function getConfig(env?: string): DeploymentConfig {
  const defaults = getDefaultConfig();
  if (!env) {
    return defaults;
  }
  const overrides = ENV_OVERRIDES[env];
  if (!overrides) {
    return defaults;
  }
  const merged: DeploymentConfig = deepMerge(defaults, overrides);
  return merged;
}
