import os

# The examples read the secret of the service from the environment, as a
# deployment does. Some of them read it when pytest imports them.
os.environ.setdefault("SCIM_SECRET", "a secret every worker shares")
