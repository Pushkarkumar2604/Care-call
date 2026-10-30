// CareCall's sample data lives here. Later, a backend can replace these arrays.
const patients = [
  { name: 'Margaret Thompson', initials: 'MT', condition: 'Medication support', next: 'Today, 9:00 AM', last: 'Yesterday, 9:02 AM', status: 'On track', tone: 'avatar-gold' },
  { name: 'James Rivera', initials: 'JR', condition: 'Post-surgery recovery', next: 'Today, 10:30 AM', last: 'Yesterday, 10:28 AM', status: 'On track', tone: 'avatar-pink' },
  { name: 'Sarah Kim', initials: 'SK', condition: 'Daily wellness check', next: 'Today, 1:00 PM', last: 'Today, 8:45 AM', status: 'On track', tone: 'avatar-blue' },
  { name: 'Arthur Lewis', initials: 'AL', condition: 'Medication support', next: 'Tomorrow, 8:30 AM', last: 'Missed · Oct 29', status: 'Follow up', tone: 'avatar-green', pending: true }
];

let reminders = [
  { time: '9:00 AM', name: 'Margaret Thompson', task: 'Morning medication check-in', initials: 'MT', tone: 'tone-lilac', state: 'Scheduled', className: 'scheduled' },
  { time: '10:30 AM', name: 'James Rivera', task: 'Post-surgery wellness check', initials: 'JR', tone: 'tone-peach', state: 'Scheduled', className: 'scheduled' },
  { time: '1:00 PM', name: 'Sarah Kim', task: 'Afternoon hydration reminder', initials: 'SK', tone: 'tone-mint', state: 'Scheduled', className: 'scheduled' },
  { time: '2:30 PM', name: 'Arthur Lewis', task: 'Prescription refill follow-up', initials: 'AL', tone: 'tone-blue', state: 'Needs follow-up', className: 'overdue' }
];

const activities = [
  { kind: 'success', icon: '✓', text: '<strong>Margaret Thompson</strong> answered her medication check-in', time: '8:42 AM' },
  { kind: 'missed', icon: '↗', text: '<strong>Arthur Lewis</strong> missed a call · follow-up suggested', time: '8:15 AM' },
  { kind: 'created', icon: '+', text: 'New reminder created for <strong>Sarah Kim</strong>', time: 'Yesterday' }
];

const reminderList = document.querySelector('#reminder-list');
const patientRows = document.querySelector('#patient-rows');
const activityList = document.querySelector('#activity-list');
const modal = document.querySelector('#modal');
const form = document.querySelector('#reminder-form');
const toast = document.querySelector('#toast');

function renderReminders() {
  reminderList.innerHTML = reminders.slice(0, 4).map(item => `
    <div class="reminder-item">
      <div class="reminder-time"><strong>${item.time.split(' ')[0]}</strong>${item.time.split(' ')[1]}</div>
      <div class="reminder-copy"><strong>${item.task}</strong><small>${item.name}</small></div>
      <span class="pill ${item.className}">${item.state}</span>
    </div>`).join('');
}

function renderPatients(query = '') {
  const filtered = patients.filter(patient =>
    `${patient.name} ${patient.condition}`.toLowerCase().includes(query.toLowerCase())
  );

  patientRows.innerHTML = filtered.length
    ? filtered.map(patient => `
      <tr>
        <td>
          <div class="patient-cell">
            <span class="avatar ${patient.tone}">${patient.initials}</span>
            <span>${patient.name}<small>${patient.condition}</small></span>
          </div>
        </td>
        <td>${patient.next}</td>
        <td>${patient.last}</td>
        <td><span class="status ${patient.pending ? 'pending' : ''}">${patient.status}</span></td>
        <td><button class="row-menu" aria-label="Options for ${patient.name}">···</button></td>
      </tr>`).join('')
    : '<tr><td colspan="5">No patients match that search.</td></tr>';
}

function renderActivities() {
  activityList.innerHTML = activities.map(item => `
    <div class="activity-item">
      <span class="activity-symbol ${item.kind}">${item.icon}</span>
      <span class="activity-copy">${item.text}</span>
      <time>${item.time}</time>
    </div>`).join('');
}

function renderCallLogs() {
  const logs = [
    { patient: 'Margaret Thompson', time: 'Today, 8:42 AM', duration: '2m 14s', result: 'Answered', note: 'Medication taken' },
    { patient: 'Arthur Lewis', time: 'Today, 8:15 AM', duration: '—', result: 'No answer', note: 'Follow-up suggested' },
    { patient: 'Sarah Kim', time: 'Yesterday, 1:05 PM', duration: '1m 48s', result: 'Answered', note: 'Feeling well' }
  ];

  document.querySelector('#call-log-rows').innerHTML = logs.map(log => `
    <tr>
      <td>${log.patient}</td>
      <td>${log.time}</td>
      <td>${log.duration}</td>
      <td><span class="pill ${log.result === 'Answered' ? 'complete' : 'overdue'}">${log.result}</span></td>
      <td>${log.note}</td>
    </tr>`).join('');
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add('show');
  window.setTimeout(() => toast.classList.remove('show'), 2600);
}

function openModal() {
  modal.classList.add('open');
  modal.setAttribute('aria-hidden', 'false');
  document.querySelector('#patient-select').focus();
}

function closeModal() {
  modal.classList.remove('open');
  modal.setAttribute('aria-hidden', 'true');
}

document.querySelector('#patient-select').innerHTML += patients
  .map(patient => `<option value="${patient.name}">${patient.name}</option>`)
  .join('');

document.querySelector('#reminder-date').value = new Date().toISOString().slice(0, 10);
document.querySelector('#patient-search').addEventListener('input', event =>
  renderPatients(event.target.value)
);

document.querySelector('#new-reminder').addEventListener('click', openModal);
document.querySelector('#close-modal').addEventListener('click', closeModal);
document.querySelector('#cancel-modal').addEventListener('click', closeModal);

modal.addEventListener('click', event => {
  if (event.target === modal) closeModal();
});

document.addEventListener('keydown', event => {
  if (event.key === 'Escape') closeModal();
});

form.addEventListener('submit', event => {
  event.preventDefault();

  const patientName = document.querySelector('#patient-select').value;
  const patient = patients.find(item => item.name === patientName);
  const title = document.querySelector('#reminder-title').value.trim();
  const time = document.querySelector('#reminder-time').value;
  const [hour, minute] = time.split(':').map(Number);
  const formattedTime = `${(hour % 12 || 12)}:${String(minute).padStart(2, '0')} ${hour >= 12 ? 'PM' : 'AM'}`;

  reminders.unshift({
    time: formattedTime,
    name: patient.name,
    task: title,
    initials: patient.initials,
    tone: 'tone-lilac',
    state: 'Scheduled',
    className: 'scheduled'
  });

  activities.unshift({
    kind: 'created',
    icon: '+',
    text: `New reminder created for <strong>${patient.name}</strong>`,
    time: 'Just now'
  });

  renderReminders();
  renderActivities();
  form.reset();
  document.querySelector('#reminder-date').value = new Date().toISOString().slice(0, 10);
  document.querySelector('#reminder-time').value = '09:00';
  closeModal();
  showToast('Reminder saved. Your patient is all set!');
});

document.querySelector('#filter-button').addEventListener('click', () =>
  showToast('Showing all patient statuses')
);

document.querySelectorAll('[data-page]').forEach(link => {
  link.addEventListener('click', event => {
    event.preventDefault();

    const page = link.dataset.page;
    document.querySelectorAll('.nav-link').forEach(nav =>
      nav.classList.toggle('active', nav.dataset.page === page)
    );

    document.querySelector('#page-name').textContent = page
      .split('-')
      .map(word => word[0].toUpperCase() + word.slice(1))
      .join(' ');

    if (page === 'patients') {
      document.querySelector('.patients-panel').scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else if (page === 'reminders') {
      document.querySelector('.reminders-panel').scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else if (page === 'call-logs') {
      document.querySelector('#call-logs-section').scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    document.querySelector('#sidebar').classList.remove('open');
  });
});

document.querySelector('#menu-toggle').addEventListener('click', () =>
  document.querySelector('#sidebar').classList.toggle('open')
);

renderReminders();
renderPatients();
renderActivities();
renderCallLogs();