// PASSWORD SHOW / HIDE

function togglePassword(inputId, button) {

    const input = document.getElementById(inputId);

    if (!input) {
        console.error("Password input not found:", inputId);
        return;
    }

    if (input.type === "password") {

        input.type = "text";
        button.textContent = "HIDE";

    } else {

        input.type = "password";
        button.textContent = "SHOW";

    }
}


// FORM SUBMIT ANIMATION

document.addEventListener("DOMContentLoaded", function () {

    const forms = document.querySelectorAll("form");

    forms.forEach(function (form) {

        form.addEventListener("submit", function () {

            const button = form.querySelector(".primary-btn");

            if (button) {

                button.style.opacity = "0.75";
                button.style.pointerEvents = "none";
                button.textContent = "PROCESSING...";

            }

        });

    });

});