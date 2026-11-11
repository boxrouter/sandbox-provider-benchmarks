// SPDX-FileCopyrightText: BoxRouter, Inc.
// SPDX-License-Identifier: Apache-2.0
// Thin resolver for the compiled source templates; grammar is compiled by the producer.
export type MetricLabelContext = { target?: string; statistic?: string; unit?: string; comparison?: string };
export type MetricLabelCatalog = {
  metrics: Record<string, { target?: string; document: string; name: string; quantity?: string | null; unit?: string | null; statistic?: string | null; labels: Record<string, Record<string, string>>; direction?: string | null }>;
  statistics: Record<string, string>;
  statistics_sentence?: Record<string, string>;
  comparison_template: string;
  chart_bindings?: Record<string, Record<string, { metric: string; statistic: string }>>;
};
export type MetricLabelForm = "short" | "long" | "axis" | "caption";

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

/** A metric is referenced by its document's id and its name (`<document>/<metric>`). */
export function metricReference(catalog: MetricLabelCatalog, documentId: string, metricName: string): string | undefined {
  const reference = `${documentId}/${metricName}`;
  return catalog.metrics[reference] ? reference : undefined;
}

// A chart chooses its exact output at the owner; no first-metric inference.
export function chartMetricReference(catalog: MetricLabelCatalog, documentId: string, chart: string): string | undefined {
  return catalog.chart_bindings?.[documentId]?.[chart]?.metric;
}

export function labelForChart(catalog: MetricLabelCatalog, documentId: string, chart: string, form: MetricLabelForm, context: MetricLabelContext = {}): string | undefined {
  const binding = catalog.chart_bindings?.[documentId]?.[chart];
  return binding ? createMetricLabeler(catalog)(binding.metric, form, binding.statistic === "selected" ? context : { ...context, statistic: binding.statistic }) : undefined;
}
