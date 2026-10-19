/* İlk kullanım turu: finansal veri değiştirmez, tercih yalnızca bu tarayıcıda saklanır. */
(function () {
  "use strict";
  const KEY = "touche.finans.onboarding.v1.dismissed";
  const steps = [
    ["Bugünkü görünüm", "Nakit, borç, alacak, net sonuç ve yatırım kapasitesini tek özet üzerinde izleyin."],
    ["CFO Asistanı", "Doğal dille sorun veya belge yükleyin. Asistan kayıt öncesi taslak ve kanıt gösterir."],
    ["Onay ve belgeler", "Onay şifresi girilmeden Excel ve finans defteri değişmez. Hatalı alanları onaydan önce düzeltebilirsiniz."],
    ["Faaliyet ve projeler", "Holding, faaliyet, proje ve iş kalemi detaylarına menüden ilerleyin; erişim kapsamınız korunur."],
    ["Rapor ve kurtarma", "Rakamların kaynağını inceleyin. Bekleyen recovery veya tutarsızlık varsa sistem bunu açıkça bildirir."],
  ];
  const $ = (id) => document.getElementById(id);
  let index = 0;
  function dialog() { return $("onboardingDialog"); }
  function render() {
    $("onboardingTitle").textContent = steps[index][0];
    $("onboardingText").textContent = steps[index][1];
    $("onboardingProgress").textContent = `${index + 1} / ${steps.length}`;
    $("onboardingNext").textContent = index === steps.length - 1 ? "Tamamla" : "Sonraki";
  }
  function close(save) {
    if (save || $("onboardingNever").checked) localStorage.setItem(KEY, "1");
    $("onboardingNever").checked = false;
    if (dialog().open) dialog().close();
  }
  function open(force) {
    if (!force && localStorage.getItem(KEY)) return;
    index = 0;
    render();
    // Non-modal tur: kullanıcı uygulamayı kullanırken ilgili kategoriye
    // tıklamaya devam edebilir; tur yalnızca sağ üstte yardımcı bir paneldir.
    if (!dialog().open) dialog().show();
  }
  document.addEventListener("DOMContentLoaded", () => {
    $("onboardingNext").addEventListener("click", () => {
      if (index < steps.length - 1) { index += 1; render(); } else close(true);
    });
    $("onboardingSkip").addEventListener("click", () => close(false));
    $("onboardingHelp")?.addEventListener("click", () => open(true));
    $("cfoNav")?.addEventListener("click", (event) => {
      if (event.target.closest("button[data-view]")) open(false);
    });
    window.ToucheOnboarding = { open: () => open(true) };
  });
})();
