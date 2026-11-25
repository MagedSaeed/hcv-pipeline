import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from './client';
import { fetchAllPagesEnvelope } from './queries';

export const analysisKeys = {
  all: ['analyses'],
  lists: () => ['analyses', 'list'],
  list: (scope) => ['analyses', 'list', scope],
  detail: (id) => ['analyses', 'detail', String(id)],
  report: (id, updatedAt) => ['analyses', 'report', String(id), updatedAt],
  options: (taxonomy) => ['analysisOptions', taxonomy],
};

export const ANALYSIS_POLL_INTERVAL_MS = 2000;

export function activeRunOf(analysis) {
  const runs = Object.values(analysis?.latest_runs ?? {});
  return runs.find((run) => run && (run.status === 'queued' || run.status === 'running')) ?? null;
}

export function useAnalyses(scope) {
  return useQuery({
    queryKey: analysisKeys.list(scope),
    queryFn: () => fetchAllPagesEnvelope('/analyses/', { scope }),
  });
}

export function useAnalysis(id) {
  return useQuery({
    queryKey: analysisKeys.detail(id),
    queryFn: async () => (await apiClient.get(`/analyses/${id}/`)).data,
    enabled: Boolean(id),
    refetchInterval: (query) => (activeRunOf(query.state.data) ? ANALYSIS_POLL_INTERVAL_MS : false),
  });
}

export function useAnalysisOptions(taxonomy) {
  return useQuery({
    queryKey: analysisKeys.options(taxonomy),
    queryFn: async () => (await apiClient.get('/analyses/options/', { params: { taxonomy } })).data,
    enabled: Boolean(taxonomy),
  });
}

// Keyed by updated_at, so the report follows every save of the analysis.
export function useAnalysisReport(id, updatedAt) {
  return useQuery({
    queryKey: analysisKeys.report(id, updatedAt),
    queryFn: async () => (await apiClient.get(`/analyses/${id}/report/`)).data,
    enabled: Boolean(id && updatedAt),
    placeholderData: keepPreviousData,
  });
}

// For writes that return the whole analysis: cache it and refresh the lists.
function useDetailWrite(mutationFn) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: async (data) => {
      await queryClient.cancelQueries({ queryKey: analysisKeys.detail(data.id) });
      queryClient.setQueryData(analysisKeys.detail(data.id), data);
      queryClient.invalidateQueries({ queryKey: analysisKeys.lists() });
    },
  });
}

// For writes that return a part: refetch the analysis they belong to.
function usePartWrite(id, mutationFn) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: analysisKeys.detail(id) }),
        queryClient.invalidateQueries({ queryKey: analysisKeys.lists() }),
      ]),
  });
}

export function useCreateAnalysis() {
  return useDetailWrite(async (body) => (await apiClient.post('/analyses/', body)).data);
}

export function useUpdateAnalysis() {
  return useDetailWrite(async ({ id, ...body }) => (await apiClient.patch(`/analyses/${id}/`, body)).data);
}

export function useStartNew() {
  return useDetailWrite(async (id) => (await apiClient.post(`/analyses/${id}/start-new/`)).data);
}

export function useDeleteAnalysis() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id) => apiClient.delete(`/analyses/${id}/`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: analysisKeys.lists() }),
  });
}

export function useExtract(id) {
  return usePartWrite(id, async () => (await apiClient.post(`/analyses/${id}/extract/`)).data);
}

export function useUpdateItem(id) {
  return usePartWrite(
    id,
    async ({ itemId, ...body }) => (await apiClient.patch(`/analyses/${id}/items/${itemId}/`, body)).data
  );
}

export function useAddItem(id) {
  return usePartWrite(id, async (body) => (await apiClient.post(`/analyses/${id}/items/`, body)).data);
}

export function useDeleteItem(id) {
  return usePartWrite(id, async (itemId) => apiClient.delete(`/analyses/${id}/items/${itemId}/`));
}

export function useApproveAsserted() {
  return useDetailWrite(async (id) => (await apiClient.post(`/analyses/${id}/items/approve-asserted/`)).data);
}

export function useApproveSection(id) {
  return useDetailWrite(async (section) => (await apiClient.post(`/analyses/${id}/items/approve-section/`, { section })).data);
}

export function useGenerate(id) {
  return usePartWrite(id, async () => (await apiClient.post(`/analyses/${id}/generate/`)).data);
}

export function useLabelScenarios(id) {
  return usePartWrite(id, async (body = {}) => (await apiClient.post(`/analyses/${id}/label/`, body)).data);
}

export function useCancelRun(id) {
  return usePartWrite(id, async () => (await apiClient.post(`/analyses/${id}/cancel/`)).data);
}

export function useAddScenario(id) {
  return usePartWrite(id, async (body) => (await apiClient.post(`/analyses/${id}/scenarios/`, body)).data);
}

export function useUpdateScenario(id) {
  return usePartWrite(
    id,
    async ({ scenarioId, ...body }) => (await apiClient.patch(`/analyses/${id}/scenarios/${scenarioId}/`, body)).data
  );
}

export function useDeleteScenario(id) {
  return usePartWrite(id, (scenarioId) => apiClient.delete(`/analyses/${id}/scenarios/${scenarioId}/`));
}
