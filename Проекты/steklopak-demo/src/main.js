import './style.css';
import {
  contacts,
  extras,
  header,
  hero,
  lightbox,
  process,
  production,
  quizShell,
  reviews,
  skipLink,
  works,
} from './js/render.js';
import { initHeader } from './js/header.js';
import { initQuiz } from './js/quiz.js';
import { initGallery } from './js/gallery.js';

const app = document.getElementById('app');

if (app) {
  app.innerHTML = [
    skipLink(),
    header(),
    '<main id="main">',
    hero(),
    production(),
    quizShell(),
    works(),
    extras(),
    process(),
    reviews(),
    '</main>',
    contacts(),
    lightbox(),
  ].join('');

  initHeader();
  initQuiz();
  initGallery();
}
