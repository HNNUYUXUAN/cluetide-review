import snapshot from "../bot-story-data.json";
import type { BotPrepareRequest } from "../bot-types";
import type { StepKey } from "./routes";

export const story = snapshot;
export const archiveDate = "8 Oct 2026";
export const shortHash = (value: string) => `${value.slice(0, 10)}…${value.slice(-8)}`;
export const assetUrl = (name: string) => new URL(`bot-demo/${name}`, document.baseURI).href;
export const steps = ["v1", "review", "v2"] as const;
export const stepLabels = { v1: "Original report", review: "Exact-version review", v2: "Corrected report" };
export function stepData(key: StepKey) { return story.steps.find(step => step.key === key)!; }
export function preparedRequest(key: StepKey) { return stepData(key).prepared as BotPrepareRequest; }
export const bundles = {
  v1: { file: "uni-v1.zip", archive: "37e6d1adab334a751a93da1cc5d7f8401229b333f3e3ce50353f650123c67e4b", manifest: stepData("v1").content_hash, revision: 1 },
  v2: { file: "uni-v2.zip", archive: "b8f918413e79f797b3a3606c835d64b79919ca5ecdc369e28723dffa112037d6", manifest: stepData("v2").content_hash, revision: 2 },
};

// English explanatory adaptation. Committed source files retain their original bytes.
export const chapterCopy = {
  v1: {
    heading: "Start with the evidence.",
    paragraph: "An AI investigation assembled the transfer, transaction receipt and proposal 93 material into a first report. It linked the successful receipt to a governance explanation.",
    observed: "100,000,000 UNI transferred to the dead address",
    interpretation: "Initial governance execution explanation",
    unknown: "Authorization and supply change require review",
    note: "The initial report and its evidence remain available as the exact version that was reviewed.",
  },
  review: {
    heading: "Review the exact version.",
    paragraph: "The reviewer requested a correction: distinguish a successful receipt from the scope of governance authorization. The review is bound to version 1 and its content commitment.",
    observed: "Successful transaction and receipt",
    interpretation: "Correction requested for the execution claim",
    unknown: "Governance authorization and supply change",
    note: "Review 1 stays attached to version 1, even as the report develops.",
  },
  v2: {
    heading: "What changed in v2",
    paragraph: "The correction separates an observed transfer and successful receipt from governance authorization. Supply change remains unknown.",
    observed: "Transfer, transaction fields and receipt",
    interpretation: "Bounded governance context",
    unknown: "Authorization and supply change",
    note: "Version 2 links to version 1 and preserves the original AI report, evidence and review history.",
  },
};
