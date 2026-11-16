// Fictional demo only: no requests, storage, or real reservations.
const menu = document.querySelector("#menu");
document.querySelector("#menu-toggle").addEventListener("click", () => {
  menu.hidden = !menu.hidden;
});
document.querySelector("#booking").addEventListener("submit", (event) => {
  event.preventDefault();
  document.querySelector("#booking").hidden = true;
  document.querySelector("#confirmation").hidden = false;
});
