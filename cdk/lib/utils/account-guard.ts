/**
 * Pinned defaults and the account-mismatch guard for the AIDLC platform CDK
 * app.
 *
 * The platform is deployed to a single, fixed AWS account
 * (`123456789012` / `us-east-1`); the guard halts a deploy that would
 * otherwise land in a different account because the active AWS credentials
 * (and therefore `CDK_DEFAULT_ACCOUNT`, which the CDK CLI populates from those
 * credentials) point somewhere else.
 *
 * `DEFAULT_ACCOUNT` below is this codebase's pinned deployment account
 * (`123456789012`). This project always deploys there; the account-mismatch
 * guard halts a deploy that resolves to any other account. An explicit
 * `-c account=<id>` context override still bypasses the guard for an
 * intentional one-off deploy elsewhere.
 *
 * The guard is deliberately bypassed when the caller passes an explicit
 * `-c account=<id>` context override, so deploying elsewhere intentionally
 * remains a one-flag operation. Region overrides are not gated.
 *
 * Centralizing these in a small module lets `bin/cdk.ts` stay declarative and
 * lets the guard be unit-tested without instantiating an `App`.
 *
 * Validates: Requirements 13.1, 13.2, 13.3, 13.4
 */

/**
 * Pinned default deployment account. The platform is deployed here unless the
 * caller supplies an explicit `-c account=<id>` context override (Req 13.1).
 */
export const DEFAULT_ACCOUNT = "123456789012";

/**
 * Pinned default deployment region. Region overrides via `-c region=<id>` or
 * `CDK_DEFAULT_REGION` are honored without guard intervention (Req 13.1, 13.4).
 */
export const DEFAULT_REGION = "us-east-1";

export interface AccountResolutionInputs {
  /** Value of `-c account=<id>` from CDK context, if supplied. */
  contextAccount?: string;
  /** Value of `process.env.CDK_DEFAULT_ACCOUNT`, if set. */
  envAccount?: string;
}

export interface AccountResolution {
  /** Resolved deployment account (context > env > DEFAULT_ACCOUNT). */
  account: string;
  /**
   * True iff the resolved value came from the explicit `-c account=<id>`
   * context flag. The guard treats that as the only intentional escape hatch.
   */
  isContextOverride: boolean;
}

/**
 * Resolve the deployment account using the standard CDK precedence:
 * 1. `-c account=<id>` (most explicit, intentional override),
 * 2. `CDK_DEFAULT_ACCOUNT` (set by the CDK CLI from the active AWS profile),
 * 3. the pinned `DEFAULT_ACCOUNT`.
 *
 * Returns both the resolved value and a flag identifying which source it came
 * from so the caller can apply the account-mismatch guard.
 */
export function resolveAccount(
  inputs: AccountResolutionInputs,
): AccountResolution {
  const ctx = inputs.contextAccount;
  if (typeof ctx === "string" && ctx.length > 0) {
    return { account: ctx, isContextOverride: true };
  }
  const env = inputs.envAccount;
  if (typeof env === "string" && env.length > 0) {
    return { account: env, isContextOverride: false };
  }
  return { account: DEFAULT_ACCOUNT, isContextOverride: false };
}

/**
 * Account-mismatch guard (Req 13.3).
 *
 * Unless `-c account=<id>` was supplied as an explicit override, the resolved
 * account must equal `DEFAULT_ACCOUNT`. This catches the case where the
 * active AWS credentials point to a different account (the CDK CLI surfaces
 * those credentials through `CDK_DEFAULT_ACCOUNT`) and halts before any
 * resource is deployed to the wrong account.
 *
 * The escape hatch is the `-c account=<id>` context flag (Req 13.4): when
 * supplied, the guard does not run, preserving the ability to deploy
 * elsewhere intentionally.
 *
 * @throws Error with a clear, actionable mismatch message on failure.
 */
export function assertExpectedAccount(resolution: AccountResolution): void {
  if (resolution.isContextOverride) {
    return;
  }
  if (resolution.account === DEFAULT_ACCOUNT) {
    return;
  }
  throw new Error(
    `Account mismatch: the resolved AWS account '${resolution.account}' does ` +
      `not match the expected deployment account '${DEFAULT_ACCOUNT}'. ` +
      `Configure AWS credentials for account '${DEFAULT_ACCOUNT}', or pass ` +
      `'-c account=${resolution.account}' to deploy to that account intentionally.`,
  );
}
