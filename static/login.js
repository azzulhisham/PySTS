login = document.getElementById('login')
register = document.getElementById('register')

username = document.getElementById('username')
userpwd = document.getElementById('userpwd')
usersignin = document.getElementById('usersignin')

function dispregister() {
    // login.classList.add('inactive')
    // register.classList.remove('inactive')

    login.style.left = '1500px'
    register.style.left = '500px'
}

function displogin() {
    // login.classList.remove('inactive')
    // register.classList.add('inactive')

    login.style.left = '0px'
    register.style.left = '1500px'
}

usersignin.addEventListener('click', () => {
    if (username.value == 'admin@pinc.my' && userpwd.value == '1234560') {
        window.location.href = "http://localhost:3838/playback";
    } 
})