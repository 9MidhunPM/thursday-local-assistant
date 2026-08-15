const storeKey = "ritual-habit-tracker-v1";
const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function dateKey(date) {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}
function daysAgo(amount) {
  const date = new Date();
  date.setDate(date.getDate() - amount);
  return dateKey(date);
}
const todayKey = dateKey(new Date());

const seedData = {
  habits: [
    { id: 1, name: "Morning movement", icon: "☀", completions: [daysAgo(0), daysAgo(1), daysAgo(2), daysAgo(3)] },
    { id: 2, name: "Drink 8 glasses of water", icon: "◌", completions: [daysAgo(1), daysAgo(2), daysAgo(3), daysAgo(4)] },
    { id: 3, name: "Read for 20 minutes", icon: "✎", completions: [daysAgo(0), daysAgo(1), daysAgo(2)] },
    { id: 4, name: "Journal one thought", icon: "♡", completions: [daysAgo(2), daysAgo(3)] }
  ],
  moods: {},
  dark: false
};

let state = JSON.parse(localStorage.getItem(storeKey) || "null") || JSON.parse(JSON.stringify(seedData));
state.habits = state.habits || [];
state.moods = state.moods || {};

const habitList = document.querySelector("#habitList");
const habitTemplate = document.querySelector("#habitTemplate");
const dialog = document.querySelector("#habitDialog");
const habitForm = document.querySelector("#habitForm");
let selectedIcon = "☀";

function save() { localStorage.setItem(storeKey, JSON.stringify(state)); }
function isDone(habit, date = todayKey) { return habit.completions.includes(date); }
function getStreak(habit) {
  let streak = 0;
  for (let offset = 0; ; offset += 1) {
    if (!habit.completions.includes(daysAgo(offset))) break;
    streak += 1;
  }
  return streak;
}
function getOverallStreak() {
  let streak = 0;
  for (let offset = 0; ; offset += 1) {
    const date = daysAgo(offset);
    if (!state.habits.length || !state.habits.some((habit) => habit.completions.includes(date))) break;
    streak += 1;
  }
  return streak;
}
function formatDate() {
  return new Date().toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" }).toUpperCase();
}
function completedToday() { return state.habits.filter((habit) => isDone(habit)).length; }
function weekData() {
  return Array.from({ length: 7 }, (_, index) => {
    const offset = 6 - index;
    const date = daysAgo(offset);
    return { date, label: offset === 0 ? "Today" : dayNames[new Date(`${date}T12:00:00`).getDay()], count: state.habits.filter((habit) => isDone(habit, date)).length };
  });
}
function renderCheckin() {
  const mood = state.moods[todayKey];
  const labels = { rough: "You showed up. That is enough for today.", steady: "A steady start makes room for good things.", bright: "Bring that bright energy to one small ritual." };
  document.querySelectorAll("[data-mood]").forEach((button) => button.classList.toggle("selected", button.dataset.mood === mood));
  document.querySelector("#checkinNote").textContent = mood ? labels[mood] : "Not checked in yet.";
}
function renderHabits() {
  habitList.innerHTML = "";
  state.habits.forEach((habit) => {
    const node = habitTemplate.content.cloneNode(true);
    const item = node.querySelector("li");
    const done = isDone(habit);
    item.classList.toggle("done", done);
    node.querySelector(".habit-icon").textContent = habit.icon;
    node.querySelector(".habit-name").textContent = habit.name;
    const streak = getStreak(habit);
    node.querySelector(".habit-meta").textContent = done ? "COMPLETE FOR TODAY" : "READY WHEN YOU ARE";
    node.querySelector(".habit-streak").textContent = streak ? `♨ ${streak} day${streak === 1 ? "" : "s"}` : "Start a streak";
    const check = node.querySelector(".habit-check");
    check.setAttribute("aria-label", `${done ? "Mark incomplete" : "Complete"}: ${habit.name}`);
    check.addEventListener("click", () => toggleHabit(habit.id));
    node.querySelector(".delete-habit").addEventListener("click", () => {
      state.habits = state.habits.filter((entry) => entry.id !== habit.id);
      save(); render();
    });
    habitList.append(node);
  });
  document.querySelector("#emptyState").hidden = state.habits.length !== 0;
}
function renderMetrics() {
  const complete = completedToday();
  const total = state.habits.length;
  const percent = total ? Math.round((complete / total) * 100) : 0;
  document.querySelector("#todayCount").textContent = total - complete;
  document.querySelector("#scoreValue").textContent = `${percent}%`;
  document.querySelector("#scoreRing").style.setProperty("--degree", `${percent * 3.6}deg`);
  document.querySelector("#scoreTitle").textContent = percent === 100 ? "Beautiful work" : percent ? "You’re on your way" : "Let’s begin";
  document.querySelector("#scoreDetail").textContent = `${complete} of ${total} ritual${total === 1 ? "" : "s"} complete`;
  const streak = getOverallStreak();
  document.querySelector("#streakValue").textContent = `${streak} day${streak === 1 ? "" : "s"}`;
  document.querySelector("#streakCopy").textContent = streak > 1 ? "You’re building a gentle rhythm." : "One day at a time is still progress.";
  document.querySelector("#welcomeText").textContent = percent === 100 ? "You kept every promise to yourself today." : complete ? "Keep the gentle momentum going." : "Small promises add up.";
}
function renderWeek() {
  const days = weekData();
  const totalPossible = state.habits.length * 7;
  const completed = days.reduce((sum, day) => sum + day.count, 0);
  const percent = totalPossible ? Math.round((completed / totalPossible) * 100) : 0;
  document.querySelector("#weekRate").textContent = `${percent}% complete`;
  document.querySelector("#miniProgress").style.width = `${percent}%`;
  document.querySelector("#weekSummary").textContent = totalPossible ? `${completed} ritual check-ins this week.` : "A fresh start awaits.";
  document.querySelector("#weekChart").innerHTML = days.map((day, index) => {
    const height = state.habits.length ? Math.max(4, Math.round((day.count / state.habits.length) * 100)) : 4;
    return `<div class="day-column ${index === 6 ? "today" : ""}" title="${day.count} rituals complete"><div class="bar-track"><div class="bar" style="height:${height}%"></div></div><span>${day.label}</span></div>`;
  }).join("");
  document.querySelector("#insightCopy").textContent = percent >= 75 ? "You are tending your routines with real consistency." : percent >= 40 ? "A few intentional check-ins can make this your strongest week yet." : "Check off a ritual today to start your story.";
}
function render() { renderCheckin(); renderHabits(); renderMetrics(); renderWeek(); }
function toggleHabit(id) {
  const habit = state.habits.find((entry) => entry.id === id);
  if (!habit) return;
  habit.completions = isDone(habit) ? habit.completions.filter((date) => date !== todayKey) : [...habit.completions, todayKey];
  save(); render();
}

document.querySelector("#dateLine").textContent = formatDate();
document.querySelectorAll("[data-mood]").forEach((button) => button.addEventListener("click", () => { state.moods[todayKey] = button.dataset.mood; save(); renderCheckin(); }));
document.querySelector("#openHabitForm").addEventListener("click", () => { habitForm.reset(); selectedIcon = "☀"; document.querySelectorAll("[data-icon]").forEach((button) => button.classList.toggle("selected", button.dataset.icon === selectedIcon)); dialog.showModal(); document.querySelector("#habitName").focus(); });
document.querySelectorAll("[data-icon]").forEach((button) => button.addEventListener("click", () => { selectedIcon = button.dataset.icon; document.querySelectorAll("[data-icon]").forEach((entry) => entry.classList.toggle("selected", entry === button)); }));
habitForm.addEventListener("submit", (event) => {
  if (event.submitter?.value !== "default") return;
  event.preventDefault();
  const name = document.querySelector("#habitName").value.trim();
  if (!name) return;
  state.habits.push({ id: Date.now(), name, icon: selectedIcon, completions: [] });
  save(); dialog.close(); render();
});
document.querySelector("#themeToggle").addEventListener("click", () => { state.dark = !state.dark; save(); applyTheme(); });
document.querySelector("#resetButton").addEventListener("click", () => { state = JSON.parse(JSON.stringify(seedData)); save(); applyTheme(); render(); });
function applyTheme() { document.body.classList.toggle("dark", state.dark); const toggle = document.querySelector("#themeToggle"); toggle.textContent = state.dark ? "☀" : "☾"; toggle.setAttribute("aria-label", `Switch to ${state.dark ? "light" : "dark"} mode`); }
applyTheme();
render();
