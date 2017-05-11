# ssh sources:
# https://www.digitalocean.com/community/tutorials/how-to-set-up-ssh-keys--2
# https://www.digitalocean.com/community/tutorials/how-to-use-ssh-keys-with-digitalocean-droplets

import os
from fabric.contrib.files import exists
from fabric.utils import abort
from fabric.api import task, env, run, sudo, local, settings, \
    get, put, shell_env, cd


# File paths
cwd = os.getcwd()

# Global settings
users = [
    ('root', 'root'),
    ('ben', 'user'),
]
params = {
    'temp_file': os.path.join(cwd, 'temp'),
    'public_key': '/Users/belliot-mac/.ssh/id_rsa.pub',
    'local_sshd_config': os.path.join(cwd, 'sshd_config'),
    'remote_sshd_config': '/etc/ssh/sshd_config',
}


def require_environment():
    """
    Helper method to ensure that tasks aren't run in development
    """
    if not hasattr(env, 'environment'):
        raise NotImplementedError(
            'An environment task like @production must be the first task')


@task
def production():
    """Staging server settings. Must be first task!"""
    env.hosts = ['production.bennyda.ninja']
    env.app = 'mmpl'
    env.environment = 'production'
    env.user = 'root'
    env.django_user = 'ben'
    env.path = '/var/www/%(app)s/%(environment)s' % env
    env.media = '/media/%(app)s/%(environment)s' % env


@task
def staging():
    """Production server settings. Must be first task!"""
    env.hosts = ['staging.bennyda.ninja']
    env.app = 'mmpl'
    env.environment = 'staging'
    env.user = 'root'
    env.django_user = 'ben'
    env.path = '/var/www/%(app)s/%(environment)s' % env
    env.media = '/media/%(app)s/%(environment)s' % env


@task
def provision_host():
    require_environment()

    install_python()

    for user in users:
        home = get_home_path(user)
        remote_file = os.path.join(home, '.ssh/authorized_keys')
        if 'user' in user[1]:
            print 'adding user %s' % user[0]
            add_user(user)
            make_user_sudo(user)
            print 'user added'
            add_public_key_to_remote_user(remote_file, user[0], home)
            rm_temp_file()
        elif 'system' in user[1]:
            print 'adding system user %s' % user[0]
            add_system_user(user)
            print 'user added'
        elif 'root' in user[1]:
            add_public_key_to_remote_user(remote_file, user[0], home)
            rm_temp_file()

    secure_ssh()

    with settings(prompts={'Do you want to continue? [Y/n] ': 'Y'}):
        run('apt-get update && apt-get upgrade')

    run_ansible()

    create_django_superuser()


@task
def run_ansible():
    require_environment()
    local("ansible-playbook \
          -i %(environment)s \
          --vault-password-file secrets/vault_password.txt \
          -v webservers.yml" % env)


@task
def create_django_superuser():
    require_environment()
    with shell_env(DJANGO_SETTINGS_MODULE='mmpl.settings.%(environment)s' % env):
        with cd(env.path):
            venv = 'source venv/bin/activate'
            createsuperuser = 'repo/site/manage.py createsuperuser --username=%(django_user)s' % env
            sudo('%s && %s' % (venv, createsuperuser))


def rsync_static_files():
    require_environment()
    # copy static files from


def install_python():
    with settings(prompts={'Do you want to continue? [Y/n] ': 'Y'}):
        run('apt-get update')
        run('apt-get install python')


# post setup_ssh function
def write_config_to_tempfile(temp_path, write):
    if not os.path.exists(temp_path):
        local('touch %s' % temp_path)

    if type(write) == str:
        with open(temp_path, 'a') as t:
            t.write(write % params)
    elif type(write) == file:
        with open(temp_path, 'a') as t:
            t.write(write.read().rstrip())


def get_config(temp_path, remote_path):
    get(local_path=temp_path, remote_path=remote_path)


def put_config(temp_path, remote_path):
    put(local_path=temp_path, remote_path=remote_path)


def add_public_key_to_remote_user(remote_file, user, home):
    """Pulls together the confirmation logic, key
    addition and temp file removal"""
    if os.path.exists(params['public_key']):
        with open(params['public_key'], 'r') as pub:
            print "Adding Public Key to user '%s':\n" % user
            print "%s\n" % pub.read()
        if check_key_authorized(params['temp_file'], remote_file):
            print "Key already present, continuing"
        else:
            sudo('mkdir -p %s.ssh' % home)
            run('touch %s.ssh/authorized_keys' % home)
            with open(params['public_key'], 'r') as l:
                write_config_to_tempfile(params['temp_file'], l)
            put_config(params['temp_file'], remote_file)
            print "Key added, continuing."
    else:
        abort('Cannot locate public key. Check fabfile or create key pair.')


def get_home_path(user):
    """Takes the user, checks for root. Returns string to user's home dir."""
    if user == 'root':
        return '/root/'
    else:
        return '/home/%s/' % user[0]


def check_key_authorized(temp_path, remote_file):
    '''
    Function takes full path to local id_rsa.pub file and full path to remote
    authorized_keys file to compare the content. Opens each file, compares the
    first line of the local file to every line of the remote file to check if
    the SSH key already exists on host. If it exists, return True. If not,
    return False
    '''
    # get remote authorized_keys file
    if exists(remote_file):
        get(local_path=temp_path, remote_path=remote_file)
        # load up local and remote files in memory
        if os.path.exists(temp_path):
            with open(params['public_key'], 'r') as l:
                with open(temp_path, 'r') as r:
                    # compare first line of pub key file to
                    # every line of remote file
                    for key in r:
                        key = key.rstrip()
                        # if matched, then pub key is
                        # in authorized_keys file, return True
                        if key == l.readline().rstrip():
                            return True

    return False


def query_yes_no(query):
    """Abstract for getting confirmation from user."""
    yes = set(['yes', 'y', 'ye', ''])
    no = set(['no', 'n'])

    while True:
        choice = raw_input(query + ' [Y/n]  ').lower()
        if choice in yes:
            return True
        elif choice in no:
            return False
        else:
            print "Please respond with 'yes' or 'no'"


def add_user(user):
    """Adds users given user name."""
    with settings(warn_only=True):
        run('adduser %s' % user[0])


def add_system_user(user):
    """Adds users given user name."""
    with settings(warn_only=True):
        run('adduser --system --no-create-home --group %s' % user[0])


def make_user_sudo(user):
    with settings(warn_only=True):
        run('gpasswd -a %s sudo' % user[0])


def secure_ssh():
    """Takes the path to the remote authorized_keys file. Puts the
    local sshd config in the remote config directory if the public
    key is present in the remote authorized_keys file. Reloads SSH
    service and cleans up temporary files."""
    # Lock down ssh to certificate authentication only.
    put_config(params['local_sshd_config'], params['remote_sshd_config'])
    print "Access to remote host secured to only SSH key."


def rm_temp_file():
    """Removes or warns."""
    try:
        os.remove(params['temp_file'])
    except OSError:
        print OSError
        print "Failed to remove file. Check manually."
