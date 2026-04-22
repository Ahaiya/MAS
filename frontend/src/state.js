export function createInitialState(data) {
  const defaultSample = data.samples[0];
  const defaultDimension = defaultSample.dimensionOrder[0];
  const defaultObservations = defaultSample.observationsByDimension[defaultDimension] || [];
  const defaultObservation = defaultObservations[0];

  // Capture original AI-generated scores and feedback before any user edits.
  // Shape: { [sampleId]: { [observationId]: { score, feedback } } }
  const originalValues = {};
  for (const sample of data.samples) {
    originalValues[sample.id] = {};
    for (const observations of Object.values(sample.observationsByDimension)) {
      for (const obs of observations) {
        originalValues[sample.id][obs.id] = {
          score: obs.score,
          feedback: obs.feedback,
        };
      }
    }
  }

  return {
    data: structuredClone(data),
    originalValues,
    activeStudent: defaultSample.id,
    activeDimension: defaultDimension,
    activeObservationId: defaultObservation?.id || null,
    openObservationIds: defaultObservations.map((item) => item.id),
    activeSpanId: defaultObservation?.evidence[0]?.spanId || null,
    releasedStudents: Object.fromEntries(data.samples.map((sample) => [sample.id, false])),
    selectionMenu: {
      visible: false,
      x: 0,
      y: 0,
      entryId: null,
      text: "",
    },
    inspectorScrollTop: 0,
    pageScrollY: 0,
    toast: "",
    toastTimer: null,
  };
}
