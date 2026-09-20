import {chmodSync, readFileSync, writeFileSync} from "node:fs";
import {createRequire} from "node:module";
import {join} from "node:path";
import {pathToFileURL} from "node:url";

const [personalRuntimeRoot, configPath] = process.argv.slice(2);
if (personalRuntimeRoot === undefined || configPath === undefined) {
  throw new Error("usage: personal_runtime_admission_owner_probe.mjs <personal-runtime-root> <config-json>");
}

// Import only Personal Runtime's public package root. The probe never reaches
// into lib/store, lib/composition, or another implementation-only module.
const packageRequire = createRequire(join(personalRuntimeRoot, "package.json"));
const runtime = await import(pathToFileURL(packageRequire.resolve("@personal-runtime/core")).href);
const config = JSON.parse(readFileSync(configPath, "utf8"));
const now = "2026-09-21T00:00:00.000Z";

const database = runtime.openRuntimeDatabase(config.databasePath, {
  runtimeInstanceId: "ccg-public-admission-probe",
  now: () => now,
});
let token;
try {
  const policyUnsigned = {policyId: "policy-main", revision: 1, body: {}, createdAt: now};
  const policy = {...policyUnsigned, digest: runtime.digestJson(policyUnsigned)};
  runtime.insertImmutableRevision(database, "policy_revisions", {
    id: policy.policyId,
    revision: policy.revision,
    documentJson: JSON.stringify(policy),
    contentDigest: runtime.digestJson(policy),
    createdAt: now,
  });

  const profileUnsigned = {
    profileId: "profile-main",
    revision: 1,
    harnessRef: "codex",
    backendRef: "local",
    executionControlEvidenceRefs: [],
  };
  const profile = {...profileUnsigned, digest: runtime.digestJson(profileUnsigned)};
  runtime.insertImmutableRevision(database, "execution_profile_revisions", {
    id: profile.profileId,
    revision: profile.revision,
    documentJson: JSON.stringify(profile),
    contentDigest: runtime.digestJson(profile),
    createdAt: now,
  });

  const constraintUnsigned = {
    constraintSetId: "constraints-main",
    revision: 1,
    clauses: [],
    derivedFrom: [],
  };
  const constraint = {...constraintUnsigned, digest: runtime.digestJson(constraintUnsigned)};
  runtime.insertConstraintSet(database, {
    id: constraint.constraintSetId,
    revision: constraint.revision,
    documentJson: JSON.stringify(constraint),
    contentDigest: runtime.digestJson(constraint),
    createdAt: now,
  });
  token = runtime.issueLocalCallerIdentity(database, {
    callerId: "ccg-caller",
    label: "CCG public admission qualification",
    scopes: ["delegation.commit"],
    now,
  }).token;
} finally {
  database.close();
}

writeFileSync(config.tokenPath, token, {encoding: "utf8", mode: 0o600});
chmodSync(config.tokenPath, 0o600);
const owner = await runtime.startPersonalRuntimeAdmissionOwner({
  databasePath: config.databasePath,
  socketPath: config.socketPath,
  admission: {
    authorityByCaller: {"ccg-caller": "ccg"},
    resolveDecisionContext: () => ({
      decisionCallerId: "ccg-caller",
      selectionAuthority: "ccg",
      policyRevision: "policy-main@1",
      constraintSetRef: "constraints-main@1",
      recoveryPolicyRef: "recovery-default@1",
    }),
    now: () => now,
  },
  reconcile: () => undefined,
  databaseOptions: {
    startupMode: "normal_restart",
    runtimeInstanceId: "ccg-public-admission-probe",
    now: () => now,
  },
});

process.stdout.write(`${JSON.stringify({ready: true, socketPath: owner.socketPath})}\n`);
const completion = JSON.parse(await new Promise((resolve) => {
  process.stdin.once("data", (chunk) => resolve(chunk.toString("utf8")));
}));
await owner.close();

const readDatabase = runtime.openRuntimeReadDatabase(config.databasePath);
try {
  const delegation = runtime.readDelegationModel(
    readDatabase,
    "delegation-ccg-exact-pin",
    "2026-09-21T00:00:02.000Z",
  );
  const outbox = runtime.getOutboxCommand(readDatabase, completion.outboxCommandId);
  process.stdout.write(`${JSON.stringify({delegation, outbox})}\n`);
} finally {
  readDatabase.close();
}
