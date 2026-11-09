// SPDX-FileCopyrightText: BoxRouter, Inc.
// SPDX-License-Identifier: Apache-2.0
// Thin resolver for the compiled source templates; grammar is compiled by the producer.
export type MetricLabelContext = { target?: string; statistic?: string; unit?: string; comparison?: string };
export type MetricLabelCatalog = {
  metrics: Record<string, { target?: string; document: string; version: string; name: string; quantity?: string | null; unit?: string | null; statistic?: string | null; labels: Record<string, Record<string, string>>; direction?: string | null }>;
  statistics: Record<string, string>;
  statistics_sentence?: Record<string, string>;
  comparison_template: string;
  chart_bindings?: Record<string, Record<string, { metric: string; statistic: string }>>;
};
export type MetricLabelForm = "short" | "long" | "axis" | "caption";
type MetricLabelEntry = MetricLabelCatalog["metrics"][string];
/** The released form: one entry per document metric and run of versions whose entries are equal apart from the version. */
export type MetricLabelRelease = Omit<MetricLabelCatalog, "metrics"> & { metric_groups: (Omit<MetricLabelEntry, "version"> & { versions: string[] })[] };

/** Every metric entry by its qualified reference (`<document>@<version>/<metric>`), as the groups hold them. */
export function expandMetricLabels(release: MetricLabelRelease): MetricLabelCatalog {
  const { metric_groups: groups, ...rest } = release;
  const metrics: MetricLabelCatalog["metrics"] = {};
  for (const { versions, ...entry } of groups) for (const version of versions) metrics[`${entry.document}@${version}/${entry.name}`] = { ...entry, version };
  return { ...rest, metrics };
}

export function createMetricLabeler(catalog: MetricLabelCatalog) {
  return function labelFor(metricId: string, form: MetricLabelForm, context: MetricLabelContext = {}): string | undefined {
    if (form === "short" && context.comparison) {
      const left: string | undefined = labelFor(metricId, form);
      const right: string | undefined = labelFor(context.comparison, form);
      return left && right ? catalog.comparison_template.replace("{left.quantity}", left).replace("{right.quantity}", right) : undefined;
    }
    const metric = catalog.metrics[metricId];
    const template = metric?.labels[form]?.[context.target ?? metric?.target ?? "metric"];
    if (!metric || !template) return undefined;
    const statistic = context.statistic ?? metric.statistic;
    const values: Record<string, string | undefined> = { unit: context.unit ?? metric.unit ?? undefined, statistic: statistic ? (form === "caption" ? catalog.statistics_sentence?.[statistic] ?? catalog.statistics[statistic] : catalog.statistics[statistic]) : undefined };
    const result = template.replace(/\{([\w.]+)\}/g, (token, key: string) => values[key] ?? token).replace(/\s+/g, " ").trim();
    return /\{[\w.]+\}/.test(result) ? undefined : result;
  };
}

export function metricReference(catalog: MetricLabelCatalog, documentId: string, metricName: string, version?: string): string | undefined {
  return Object.entries(catalog.metrics)
    .filter(([, metric]) => metric.document === documentId && metric.name === metricName && (version === undefined || metric.version === version))
    .sort(([, a], [, b]) => {
      const left = a.version.split(".").map(Number);
      const right = b.version.split(".").map(Number);
      for (let i = 0; i < left.length; i += 1) if (left[i] !== right[i]) return right[i] - left[i];
      return 0;
    })[0]?.[0];
}

// A chart chooses its exact output at the owner; no first-metric or latest-version inference.
export function chartMetricReference(catalog: MetricLabelCatalog, documentId: string, version: string, chart: string): string | undefined {
  return catalog.chart_bindings?.[`${documentId}@${version}`]?.[chart]?.metric;
}

export function labelForChart(catalog: MetricLabelCatalog, documentId: string, version: string, chart: string, form: MetricLabelForm, context: MetricLabelContext = {}): string | undefined {
  const binding = catalog.chart_bindings?.[`${documentId}@${version}`]?.[chart];
  return binding ? createMetricLabeler(catalog)(binding.metric, form, binding.statistic === "selected" ? context : { ...context, statistic: binding.statistic }) : undefined;
}
