import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const [personalRuntimeRoot, inputPath] = process.argv.slice(2);
if (personalRuntimeRoot === undefined || inputPath === undefined) {
  throw new Error("usage: personal_runtime_admission_probe.mjs <personal-runtime-root> <input-json>");
}

const built = (path) => pathToFileURL(join(personalRuntimeRoot, "lib", path)).href;
const contracts = await import(built("contracts/index.js"));
const admissionStore = await import(built("store/delegation-store.js"));
const authorization = await import(built("auth/local-caller.js"));
const databaseModule = await import(built("store/database.js"));
const immutableRevisions = await import(built("store/immutable-revisions.js"));
const outboxStore = await import(built("outbox/store.js"));

const probe = JSON.parse(readFileSync(inputPath, "utf8"));
const parsed = contracts.parseDelegationAdmissionCommand(probe.command);
const decision = parsed.payload.decision;
const resolvedTargetDigest = contracts.digestJson(decision.resolvedTarget);
if (decision.targetDigest !== resolvedTargetDigest) {
  throw new Error("CCG decision targetDigest does not match PR canonical JSON");
}
if (decision.targetDigest === probe.codingTargetDigest) {
  throw new Error("CCG coding target digest leaked into PR decision.targetDigest");
}
if (parsed.payloadDigest !== contracts.digestJson(parsed.payload)) {
  throw new Error("CCG payloadDigest does not match PR canonical JSON");
}

const now = "2026-09-21T00:00:00.000Z";
const database = databaseModule.openRuntimeDatabase(":memory:", {
  runtimeInstanceId: "ccg-exact-pin-probe",
  now: () => now,
});
try {
  const policyUnsigned = {
    policyId: "policy-main",
    revision: 1,
    body: {},
    createdAt: now,
  };
  const policy = { ...policyUnsigned, digest: contracts.digestJson(policyUnsigned) };
  immutableRevisions.insertImmutableRevision(database, "policy_revisions", {
    id: policy.policyId,
    revision: policy.revision,
    documentJson: JSON.stringify(policy),
    contentDigest: contracts.digestJson(policy),
    createdAt: now,
  });

  const profileUnsigned = {
    profileId: "profile-main",
    revision: 1,
    harnessRef: "codex",
    backendRef: "local",
    executionControlEvidenceRefs: [],
  };
  const profile = { ...profileUnsigned, digest: contracts.digestJson(profileUnsigned) };
  immutableRevisions.insertImmutableRevision(database, "execution_profile_revisions", {
    id: profile.profileId,
    revision: profile.revision,
    documentJson: JSON.stringify(profile),
    contentDigest: contracts.digestJson(profile),
    createdAt: now,
  });

  const constraintUnsigned = {
    constraintSetId: "constraints-main",
    revision: 1,
    clauses: [],
    derivedFrom: [],
  };
  const constraintSet = {
    ...constraintUnsigned,
    digest: contracts.digestJson(constraintUnsigned),
  };
  immutableRevisions.insertConstraintSet(database, {
    id: constraintSet.constraintSetId,
    revision: constraintSet.revision,
    documentJson: JSON.stringify(constraintSet),
    contentDigest: contracts.digestJson(constraintSet),
    createdAt: now,
  });

  const callerToken = authorization.issueLocalCallerIdentity(database, {
    callerId: "ccg-caller",
    label: "CCG exact-pin qualification",
    scopes: ["delegation.commit"],
    now,
  }).token;
  const service = admissionStore.createDelegationAdmissionService(database, {
    authorityByCaller: { "ccg-caller": "ccg" },
    resolveDecisionContext: () => ({
      decisionCallerId: "ccg-caller",
      selectionAuthority: "ccg",
      policyRevision: "policy-main@1",
      constraintSetRef: "constraints-main@1",
      recoveryPolicyRef: "recovery-default@1",
    }),
    now: () => now,
  });

  const receipt = service.commit(callerToken, parsed);
  const outboxCount = database
    .prepare("SELECT COUNT(*) AS count FROM outbox_commands WHERE target_system = 'fabric'")
    .get();
  if (outboxCount.count !== 1) {
    throw new Error(`expected exactly one Fabric outbox row, found ${outboxCount.count}`);
  }
  const outbox = outboxStore.getOutboxCommand(database, receipt.outboxCommandId);
  if (outbox?.commandType !== "fabric.ensure") {
    throw new Error("recorded admission did not create the Fabric ensure outbox command");
  }
  if (outbox.payload.targetDigest !== resolvedTargetDigest) {
    throw new Error("Fabric outbox target digest differs from the admitted resolvedTarget digest");
  }
  process.stdout.write(JSON.stringify({ receipt, fabricOutboxCount: outboxCount.count }));
} finally {
  database.close();
}
