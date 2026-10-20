/**
 * Unit tests for the deployment account-mismatch guard (task 9.1).
 *
 * The guard runs at CDK synth time (in `bin/cdk.ts`). It compares the
 * resolved AWS account to the pinned default and halts with a clear error
 * when no explicit `-c account=<id>` override has been supplied. The escape
 * hatch for intentional cross-account deploys is the same context flag.
 *
 * Validates: Requirements 13.1, 13.2, 13.3, 13.4
 */

import {
  DEFAULT_ACCOUNT,
  DEFAULT_REGION,
  assertExpectedAccount,
  resolveAccount,
} from "../lib/utils/account-guard";

describe("pinned defaults (Req 13.1)", () => {
  test("default account is the pinned platform account", () => {
    expect(DEFAULT_ACCOUNT).toBe("123456789012");
  });

  test("default region is us-east-1", () => {
    expect(DEFAULT_REGION).toBe("us-east-1");
  });
});

describe("resolveAccount precedence (Req 13.1, 13.4)", () => {
  test("falls back to the pinned default when no inputs are supplied", () => {
    const r = resolveAccount({});
    expect(r).toEqual({
      account: DEFAULT_ACCOUNT,
      isContextOverride: false,
    });
  });

  test("uses CDK_DEFAULT_ACCOUNT when set and no context override", () => {
    const r = resolveAccount({ envAccount: "111122223333" });
    expect(r).toEqual({
      account: "111122223333",
      isContextOverride: false,
    });
  });

  test("'-c account=<id>' takes precedence over the env var", () => {
    // Context overrides env (Req 13.4): the most explicit signal wins so a
    // caller can intentionally redirect a deploy without unsetting CDK_*.
    const r = resolveAccount({
      contextAccount: "999988887777",
      envAccount: "111122223333",
    });
    expect(r).toEqual({
      account: "999988887777",
      isContextOverride: true,
    });
  });

  test("empty-string inputs are treated as 'not supplied'", () => {
    // An empty string is the existing `cdk.ts` falsy-fallback shape (the old
    // `|| process.env.CDK_DEFAULT_ACCOUNT || DEFAULT_ACCOUNT` chain). The
    // resolver preserves that semantics so the guard runs on the next source.
    const r = resolveAccount({ contextAccount: "", envAccount: "" });
    expect(r).toEqual({
      account: DEFAULT_ACCOUNT,
      isContextOverride: false,
    });
  });
});

describe("assertExpectedAccount (Req 13.2, 13.3)", () => {
  test("permits the pinned default account (13.2)", () => {
    expect(() =>
      assertExpectedAccount({
        account: DEFAULT_ACCOUNT,
        isContextOverride: false,
      }),
    ).not.toThrow();
  });

  test("rejects a non-default account when no override was supplied (13.3)", () => {
    // The mismatch case the guard exists to catch: AWS credentials resolved
    // to the wrong account and no '-c account=<id>' was given to authorize
    // the deploy.
    expect(() =>
      assertExpectedAccount({
        account: "111122223333",
        isContextOverride: false,
      }),
    ).toThrow(/Account mismatch/);
  });

  test("the mismatch error names both the resolved and expected accounts", () => {
    // The user-facing message has to be actionable: the operator needs to
    // see both "what got resolved" and "what was expected" in one line.
    try {
      assertExpectedAccount({
        account: "111122223333",
        isContextOverride: false,
      });
      fail("expected assertExpectedAccount to throw");
    } catch (err) {
      const msg = (err as Error).message;
      expect(msg).toContain("111122223333");
      expect(msg).toContain(DEFAULT_ACCOUNT);
    }
  });

  test("a non-default account is permitted when '-c account=<id>' is set (13.4)", () => {
    // The only intentional escape hatch: a context-override account skips the
    // guard so cross-account deploys remain possible.
    expect(() =>
      assertExpectedAccount({
        account: "111122223333",
        isContextOverride: true,
      }),
    ).not.toThrow();
  });

  test("a context-override that happens to equal the default also passes", () => {
    expect(() =>
      assertExpectedAccount({
        account: DEFAULT_ACCOUNT,
        isContextOverride: true,
      }),
    ).not.toThrow();
  });
});

describe("end-to-end resolveAccount + assertExpectedAccount", () => {
  test("no inputs ⇒ resolves to default ⇒ guard passes (Req 13.2)", () => {
    expect(() => assertExpectedAccount(resolveAccount({}))).not.toThrow();
  });

  test("CDK_DEFAULT_ACCOUNT pointing at the wrong account ⇒ guard halts (Req 13.3)", () => {
    expect(() =>
      assertExpectedAccount(resolveAccount({ envAccount: "444455556666" })),
    ).toThrow(/Account mismatch/);
  });

  test("'-c account=<id>' for a different account ⇒ guard yields (Req 13.4)", () => {
    expect(() =>
      assertExpectedAccount(
        resolveAccount({ contextAccount: "444455556666" }),
      ),
    ).not.toThrow();
  });
});
