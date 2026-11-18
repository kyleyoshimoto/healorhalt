import SwiftUI

struct LoginView: View {
    @Environment(Store.self) private var store
    @State private var username = ""
    @State private var password = ""
    @State private var error: String?

    var body: some View {
        VStack(spacing: 16) {
            Text("Pantry")
                .font(.largeTitle.bold())
                .accessibilityIdentifier("login.title")
            if Mutation.isActive("M08") || Mutation.isActive("M21") {
                // M08 (B4): the fields are wrapped in a new accessibility container. M21 does the
                // same and breaks the password field (see credentialFields).
                VStack(spacing: 16) { credentialFields }
                    .accessibilityElement(children: .contain)
                    .accessibilityIdentifier("login.credentials")
            } else {
                credentialFields
            }
            if let error {
                Text(error)
                    .foregroundStyle(.red)
                    .accessibilityIdentifier("login.error")
            }
            if Mutation.isActive("M17") {
                // M17 (R2, bug): the Sign in button is replaced by a look-alike that doesn't
                // sign in.
                Button("Sign in with Passkey") {
                    error = "Passkeys aren't set up on this device"
                }
                .buttonStyle(.borderedProminent)
                .disabled(username.isEmpty || password.isEmpty)
                .accessibilityIdentifier("login.passkey")
            } else {
            // M11 (B2+B6): reworded "Sign in" -> "Continue", and the identifier removed.
            Button(Mutation.pick("M11", "Continue", else: "Sign in")) {
                if !store.logIn(username: username, password: password) {
                    error = "Incorrect username or password"
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(username.isEmpty || password.isEmpty)
            .accessibilityIdentifier(
                Mutation.isActive("M11")
                    ? "" : Mutation.pick("M01", "login.signIn", else: "login.submit"))
            }
        }
        .padding(32)
    }

    @ViewBuilder private var credentialFields: some View {
        TextField("Username", text: $username)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .textFieldStyle(.roundedBorder)
            .accessibilityIdentifier("login.username")
        // .oneTimeCode keeps iOS from offering "Save Password?", which would cover the app
        // after every sign-in during tests.
        // M21 (R5, bug): the password field silently ignores what is typed.
        SecureField("Password", text: Mutation.isActive("M21") ? .constant("") : $password)
            .textContentType(.oneTimeCode)
            .textFieldStyle(.roundedBorder)
            .accessibilityIdentifier("login.password")
    }
}
