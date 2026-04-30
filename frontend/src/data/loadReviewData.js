import { parseSampleMarkdown } from "./parseSampleMarkdown.js?v=20260434";

function resolveUrl(relativePath) {
  return new URL(relativePath, import.meta.url);
}

async function fetchJson(relativePath) {
  const response = await fetch(resolveUrl(relativePath));
  if (!response.ok) {
    throw new Error(`Failed to load JSON: ${relativePath}`);
  }
  return response.json();
}

async function fetchJsonIfExists(relativePath) {
  const response = await fetch(resolveUrl(relativePath));
  if (!response.ok) {
    return null;
  }
  return response.json();
}

async function fetchTextIfExists(relativePath) {
  const response = await fetch(resolveUrl(relativePath));
  if (!response.ok) {
    return null;
  }
  return response.text();
}

function parseYamlBlockValue(lines, startIndex, indent) {
  const values = [];
  let index = startIndex;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      values.push("");
      index += 1;
      continue;
    }

    const currentIndent = line.match(/^ */)?.[0].length ?? 0;
    if (currentIndent < indent) {
      break;
    }

    values.push(line.slice(indent));
    index += 1;
  }

  return {
    value: values.join("\n").replace(/\n{3,}/g, "\n\n").trim(),
    nextIndex: index,
  };
}

function parseRubricAnchors(yamlText) {
  if (!yamlText) {
    return {};
  }

  const lines = yamlText.split("\n");
  const result = {};
  let currentCode = null;
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    const codeMatch = line.match(/^\s*-\s+code:\s+"([^"]+)"/);
    if (codeMatch) {
      currentCode = codeMatch[1].replace("-", "_").toUpperCase();
      result[currentCode] = {};
      index += 1;
      continue;
    }

    const anchorMatch = line.match(/^\s+([1-5]):\s*(>.*)?$/);
    if (currentCode && anchorMatch) {
      const score = Number(anchorMatch[1]);
      const currentIndent = line.match(/^ */)?.[0].length ?? 0;
      const inline = line.replace(/^\s+[1-5]:\s*/, "").trim();

      if (inline && inline !== ">") {
        result[currentCode][score] = inline.replace(/^["']|["']$/g, "");
        index += 1;
        continue;
      }

      const parsed = parseYamlBlockValue(lines, index + 1, currentIndent + 2);
      result[currentCode][score] = parsed.value;
      index = parsed.nextIndex;
      continue;
    }

    index += 1;
  }

  return result;
}

function parseRubricSourceNames(markdownText) {
  if (!markdownText) return {};
  const result = {};
  // Matches table rows like: | A4 用户需求与痛点分析* |
  const pattern = /\|\s*([A-Z]\d+)\s+([^*|\r\n]+?)\*?\s*\|/g;
  let match;
  while ((match = pattern.exec(markdownText)) !== null) {
    result[match[1].trim()] = match[2].trim();
  }
  return result;
}

async function listDirectories(relativePath) {
  const response = await fetch(resolveUrl(relativePath));
  if (!response.ok) {
    return [];
  }

  const html = await response.text();
  const doc = new DOMParser().parseFromString(html, "text/html");
  const items = [...doc.querySelectorAll("a")]
    .map((node) => node.getAttribute("href") || "")
    .filter((href) => href.endsWith("/"))
    .map((href) => decodeURIComponent(href.replace(/\/$/, "")))
    .filter((href) => href && href !== ".." && href !== "." && !href.startsWith(".") && !href.startsWith("_"));

  return [...new Set(items)];
}

function cleanText(value) {
  return typeof value === "string" ? value.trim() : "";
}

function averageScore(items) {
  if (items.length === 0) return 0;
  return Math.round(items.reduce((sum, item) => sum + item.score, 0) / items.length);
}

function weightedAverageScore(items) {
  if (items.length === 0) return 0;
  const totalWeight = items.reduce((sum, item) => sum + (item.weight ?? 1), 0);
  if (totalWeight === 0) return 0;
  const weightedSum = items.reduce((sum, item) => sum + (item.score * (item.weight ?? 1)), 0);
  return Math.round((weightedSum / totalWeight) * 100) / 100;
}

function findTranscriptId(transcript, quote) {
  const direct = transcript.find((entry) => (entry.rawContent || entry.content).includes(quote));
  return direct?.id || null;
}

function sortDimensionCodes(codes) {
  return [...new Set(codes)].sort((left, right) => left.localeCompare(right));
}

function extractWorkflowName(markdown) {
  return markdown?.match(/- 流程名称:\s*(.+)/)?.[1]?.trim() || "自动发现产物";
}

function normalizeRaterRecord(record, decisiveId) {
  const evidenceSpanIds = record.evidence_span_ids || (record.evidence || []).map((item) => item.span_id).filter(Boolean);
  const rawScore = typeof record.score === "object" ? record.score.canonical_score : record.score;
  const recordId = record.hypothesis_id || record.rater_id;

  return {
    id: record.rater_id,
    label: record.rater_id.replace("_", " ").replace(/\b\w/g, (char) => char.toUpperCase()),
    score: rawScore,
    rationale: record.rationale || "",
    descriptor: cleanText((record.descriptor_refs || [])[0] || ""),
    evidenceSpanIds,
    decisive: Boolean(decisiveId) && recordId === decisiveId,
  };
}

function buildDimensionObservations({ feedback, hypotheses, adjudications, conflicts, rubricAnchors }, transcript) {
  const adjudicationRecords = adjudications?.adjudication_records || [];
  const conflictRecords = conflicts?.conflicts || [];
  const hypothesisRecords = hypotheses?.hypotheses || [];

  return Object.entries(feedback.dimensions).map(([dimensionId, dimension]) => {
    const dimensionHypotheses = hypothesisRecords
      .filter((item) => item.dimension_id === dimensionId)
      .sort((left, right) => left.rater_id.localeCompare(right.rater_id));
    const dimensionAdjudication = adjudicationRecords.find((item) => item.dimension_id === dimensionId);
    const dimensionConflict = conflictRecords.find((item) => item.dimension_id === dimensionId);
    const decisiveId =
      dimensionAdjudication?.resolver_hypothesis_id ||
      dimensionHypotheses.find((item) => item.score?.canonical_score === dimension.score)?.hypothesis_id ||
      null;

    const ratersSource = dimensionHypotheses.length > 0
      ? dimensionHypotheses
      : (dimension.audit?.scoring_records || []);

    return {
      id: dimensionId,
      code: dimensionId.toUpperCase(),
      title: dimension.dimension_name,
      score: dimension.score,
      weight: 1,
      feedback: dimension.feedback,
      rubric: (dimension.descriptor_refs || []).map(cleanText).filter(Boolean),
      rubricAnchors: rubricAnchors[dimensionId.toUpperCase()] || {},
      audit: {
        confidence: dimension.audit?.decision_confidence ?? 0.7,
        rationale: dimension.audit?.scorer_rationale || "",
        uncertainty: dimension.audit?.uncertainty_note || null,
        adjudicated: Boolean(dimension.audit?.was_adjudicated),
      },
      conflict: dimensionConflict
        ? {
            detail: dimensionConflict.conflict_detail,
            recommendedPath: dimensionConflict.recommended_path,
            resolution: dimensionAdjudication?.resolution_note || "",
          }
        : null,
      evidence: (dimension.evidence || []).map((item) => {
        const transcriptId = findTranscriptId(transcript, item.quote);
        return {
          spanId: item.span_id,
          transcriptId,
          quote: item.quote,
          sourceLabel: "artifact_feedback",
          supportType: "supporting",
          manual: false,
          locatable: Boolean(transcriptId),
          observationId: dimensionId,
        };
      }),
      raters: ratersSource.map((record) => normalizeRaterRecord(record, decisiveId)),
      manualEvidence: [],
    };
  });
}

async function discoverArtifactCatalog() {
  const projects = await listDirectories("../../../artifacts/");
  const samples = [];

  for (const project of projects) {
    const sampleNames = await listDirectories(`../../../artifacts/${project}/`);

    for (const sampleName of sampleNames) {
      const dimDirs = await listDirectories(`../../../artifacts/${project}/${sampleName}/`);
      const availableDimensions = [];

      for (const dim of dimDirs) {
        const feedback = await fetchJsonIfExists(`../../../artifacts/${project}/${sampleName}/${dim}/feedback.json`);
        if (feedback?.dimensions) {
          availableDimensions.push(dim.toUpperCase());
        }
      }

      if (availableDimensions.length === 0) {
        continue;
      }

      samples.push({
        project,
        sampleName,
        dimensionCodes: sortDimensionCodes(availableDimensions),
      });
    }
  }

  return samples;
}

async function loadSample(project, sampleName, dimensionCodes, dimensionNameMap = {}) {
  const sampleMarkdown = await fetchTextIfExists(`../../../data/training/${project}/${sampleName}.md`);
  const transcript = sampleMarkdown ? parseSampleMarkdown(sampleMarkdown) : [];
  const dimensionEntries = await Promise.all(dimensionCodes.map(async (code) => {
    const basePath = `../../../artifacts/${project}/${sampleName}/${code}`;
    const [feedback, hypotheses, adjudications, conflicts, rubricYaml] = await Promise.all([
      fetchJson(`${basePath}/feedback.json`),
      fetchJsonIfExists(`${basePath}/hypotheses.json`),
      fetchJsonIfExists(`${basePath}/adjudication_records.json`),
      fetchJsonIfExists(`${basePath}/conflicts.json`),
      fetchTextIfExists(`../../../configs/rubrics/dimension/${code.toLowerCase()}_rubric.yaml`),
    ]);
    const rubricAnchors = parseRubricAnchors(rubricYaml);
    return {
      code,
      observations: buildDimensionObservations(
        {
          feedback,
          hypotheses,
          adjudications,
          conflicts,
          rubricAnchors,
        },
        transcript,
      ),
    };
  }));

  const observationsByDimension = Object.fromEntries(
    dimensionEntries.map(({ code, observations }) => [code, observations]),
  );
  const dimensionMeta = Object.fromEntries(
    dimensionEntries.map(({ code, observations }) => [
      code,
      {
        code,
        title: dimensionNameMap[code] || observations[0]?.title || `${code} 维度`,
        subtitle: "自动读取该维度的证据链、评分分歧、裁决记录与最终反馈。",
      },
    ]),
  );
  const dimensionScores = Object.fromEntries(
    dimensionEntries.map(({ code, observations }) => [code, weightedAverageScore(observations)]),
  );

  return {
    id: `${project}/${sampleName}`,
    project,
    sampleName,
    name: `${sampleName}.md`,
    label: "真实样本",
    subtitle: `${project} / ${extractWorkflowName(sampleMarkdown)}`,
    transcript,
    dimensionOrder: dimensionCodes,
    dimensionMeta,
    observationsByDimension,
    dimensionScores,
  };
}

export async function loadReviewWorkbenchData() {
  const [catalog, rubricSourceText] = await Promise.all([
    discoverArtifactCatalog(),
    fetchTextIfExists("../../../configs/rubrics/source/rubric.md"),
  ]);

  if (catalog.length === 0) {
    throw new Error("未在 artifacts/{task}/{sample_name}/{dim} 下发现可展示的 feedback.json 产物。");
  }

  const dimensionNameMap = parseRubricSourceNames(rubricSourceText);

  const samples = await Promise.all(
    catalog.map((item) => loadSample(item.project, item.sampleName, item.dimensionCodes, dimensionNameMap)),
  );

  return {
    samples,
  };
}
