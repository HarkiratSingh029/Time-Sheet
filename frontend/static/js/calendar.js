/**
 * Project calendar interaction.
 *
 * The grid, the totals and the state chips are rendered on the server; this module owns
 * only the entry dialog. Days are real <button> elements, so tab order, Enter and Space
 * come from the platform rather than from a keydown handler that has to reimplement them.
 * A consultant logs time every day — needing the mouse would be a daily tax.
 */

const calendar = document.querySelector("[data-calendar]");
const dialog = document.querySelector("[data-entry-dialog]");

if (calendar && dialog) {
  function readNotes() {
    const source = document.querySelector("[data-calendar-notes]");
    if (!source) return {};
    try {
      return JSON.parse(source.textContent);
    } catch (error) {
      return {};
    }
  }

  const projectId = calendar.dataset.project;
  const notes = readNotes();

  const form = dialog.querySelector("[data-entry-form]");
  const heading = dialog.querySelector("[data-entry-heading]");
  const dayLabel = dialog.querySelector("[data-entry-day]");
  const frozenNotice = dialog.querySelector("[data-entry-frozen]");
  const taskField = dialog.querySelector("[data-entry-task]");
  const hoursField = dialog.querySelector("[data-entry-hours]");
  const detailField = dialog.querySelector("[data-entry-detail]");
  const dateField = dialog.querySelector("[data-entry-date]");
  const saveButton = dialog.querySelector("[data-entry-save]");
  const deleteButton = dialog.querySelector("[data-entry-delete]");
  const cancelButton = dialog.querySelector("[data-entry-cancel]");

  let lastFocused = null;

  function formatDay(isoDate) {
    const parsed = new Date(`${isoDate}T00:00:00`);
    return parsed.toLocaleDateString(undefined, {
      weekday: "long",
      day: "numeric",
      month: "long",
      year: "numeric",
    });
  }

  function openEntry(dayButton) {
    const day = dayButton.dataset.day;
    const noteIds = (dayButton.dataset.notes || "").split(",").filter(Boolean);
    const existing = noteIds.length > 0 ? notes[noteIds[0]] : null;

    lastFocused = dayButton;
    dateField.value = day;
    dayLabel.textContent = formatDay(day);

    if (existing) {
      heading.textContent = "Edit time";
      taskField.value = String(existing.taskId);
      hoursField.value = existing.hours;
      detailField.value = existing.detail || "";
      form.action = `/projects/${projectId}/notes/${noteIds[0]}`;
      deleteButton.formAction = `/projects/${projectId}/notes/${noteIds[0]}/delete`;

      const editable = existing.state === "draft";
      deleteButton.hidden = !editable;
      setEditable(editable);
      frozenNotice.hidden = editable;
    } else {
      heading.textContent = "Log time";
      hoursField.value = "";
      detailField.value = "";
      form.action = `/projects/${projectId}/notes`;
      deleteButton.hidden = true;
      setEditable(true);
      frozenNotice.hidden = true;
    }

    dialog.showModal();
    (taskField.disabled ? cancelButton : hoursField).focus();
  }

  function setEditable(editable) {
    for (const field of [taskField, hoursField, detailField]) {
      field.disabled = !editable;
    }
    saveButton.hidden = !editable;
  }

  function close() {
    dialog.close();
  }

  for (const dayButton of calendar.querySelectorAll("[data-day]")) {
    dayButton.addEventListener("dblclick", () => openEntry(dayButton));
    // Enter and Space already activate a button; this is the same door, not a second one.
    dayButton.addEventListener("click", (event) => {
      if (event.detail === 0) openEntry(dayButton);
    });
  }

  cancelButton.addEventListener("click", close);

  dialog.addEventListener("close", () => {
    if (lastFocused) lastFocused.focus();
  });

}
