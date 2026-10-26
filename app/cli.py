import click

from app.services.auth_service import PhoneAlreadyRegistered, ValidationError, create_admin_user
from app.utils.phone import mask_phone


def register_cli(app) -> None:
    @app.cli.command("create-admin")
    @click.option("--phone", prompt="Admin phone number")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def create_admin(phone, password):
        """Create a super-admin account (the only way admins are created)."""
        try:
            user = create_admin_user(phone, password)
        except ValidationError as exc:
            raise click.ClickException(str(exc))
        except PhoneAlreadyRegistered:
            raise click.ClickException("That phone number is already registered.")
        click.echo(f"Admin created for {mask_phone(user.phone_e164)}.")

    @app.cli.command("expire-holds")
    def expire_holds_cmd():
        """Mark expired payment holds as expired (run every minute by the worker in Bucket 7)."""
        from app.services.booking_service import expire_holds

        click.echo(f"Expired {expire_holds()} hold(s).")

    @app.cli.command("reconcile-payments")
    def reconcile_payments_cmd():
        """Ask the payment provider about payments whose callback is overdue (run every minute by the worker)."""
        from app.services.payment_service import reconcile_pending

        r = reconcile_pending()
        click.echo(f"checked {r['checked']}, resolved {r['resolved']}, timed out {r['timed_out']}.")
